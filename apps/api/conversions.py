"""Explicit browser uploads and artifact delivery for offline conversion."""
import json
import os
import re
import sqlite3
import time
import threading
import unicodedata
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from src.literature.conversion.models import MAX_FILE, MAX_FILES, MAX_BATCH
from src.persistence import conversions as store, repository as repo
from src.persistence.storage import data_directory

router = APIRouter(prefix='/conversion-batches', tags=['local-conversion'])
FORM_LIMIT = MAX_FILE + 64*1024


class ConversionDownload(FileResponse):
    def __init__(self, path, *, release, temporary=False, **kwargs):
        super().__init__(path, **kwargs)
        self.release = release
        self.temporary = temporary

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                if self.temporary:
                    self.path.unlink(missing_ok=True)
            finally:
                self.release()


class ConversionBodyLimit:
    """Bound document request bodies and concurrent multipart spooling."""
    def __init__(self, app):
        self.app = app
        self.upload_slots = threading.BoundedSemaphore(2)

    async def __call__(self, scope, receive, send):
        direct_pdf = bool(re.fullmatch(r'/projects/[^/]+/papers/import', scope.get('path', '')))
        if scope['type'] != 'http' or scope['method'] != 'POST' or not (scope['path'].startswith('/conversion-batches') or direct_pdf):
            return await self.app(scope, receive, send)
        limit = FORM_LIMIT if direct_pdf or '/files/' in scope['path'] else 128*1024
        headers = dict(scope.get('headers', []))
        try:
            length = int(headers.get(b'content-length', b'0'))
        except ValueError:
            length = limit+1
        if length > limit:
            from starlette.responses import JSONResponse
            return await JSONResponse({'detail': '上传请求超过大小限制。'}, 413)(scope, receive, send)
        used = 0
        async def bounded_receive():
            nonlocal used
            message = await receive()
            used += len(message.get('body', b''))
            if used > limit: raise HTTPException(413, '上传请求超过大小限制。')
            return message
        is_upload = direct_pdf or '/files/' in scope['path']
        if is_upload and not self.upload_slots.acquire(blocking=False):
            from starlette.responses import JSONResponse
            return await JSONResponse({'detail': '同时最多上传两个文件，请稍后重试。'}, 429)(scope, receive, send)
        try:
            await self.app(scope, bounded_receive, send)
        finally:
            if is_upload: self.upload_slots.release()


class FileSpec(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    size: int = Field(ge=0)


class BatchSpec(BaseModel):
    root_name: str = Field(min_length=1, max_length=100)
    files: list[FileSpec] = Field(min_length=1, max_length=MAX_FILES)
    encoding: str = 'auto'
    project_id: int | None = Field(default=None, gt=0)


def safe_path(value):
    value = unicodedata.normalize('NFC', value)
    parts = value.split('/')
    if len(parts) > 20 or any(not part or part in ('.', '..') or len(part) > 180
        or part[-1:] in (' ', '.') or re.search(r'[\x00-\x1f\x7f\\:<>"|?*]', part)
        or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in part)
        or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I) for part in parts):
        raise HTTPException(422, '文件名或相对目录不安全；请重命名后重试。')
    return value


def batch_for(batch_id):
    batch = store.get(batch_id)
    if not batch: raise HTTPException(404, '转换批次不存在。')
    return batch


def item_for(batch, file_id):
    item = next((f for f in batch['files'] if f['id'] == file_id), None)
    if not item: raise HTTPException(404, '转换文件不存在。')
    return item


@router.post('', status_code=201)
def create_batch(spec: BatchSpec, request: Request):
    if spec.encoding not in ('auto', 'utf-8-sig', 'utf-16', 'gb18030', 'big5', 'cp1252'):
        raise HTTPException(422, '不支持此编码。')
    if spec.project_id is not None and not repo.project_exists(spec.project_id):
        raise HTTPException(404, '当前项目不存在。')
    root = safe_path(spec.root_name)
    if '/' in root: raise HTTPException(422, '目录名称无效。')
    if sum(f.size for f in spec.files) > MAX_BATCH:
        raise HTTPException(413, '单批总文件大小超过 200 MiB。')
    paths = [safe_path(f.path) for f in spec.files]
    if len({p.casefold() for p in paths}) != len(paths):
        raise HTTPException(422, '批次中有重复路径。')
    stems = [str(PurePosixPath(p).with_suffix('.md')) for p in paths]
    candidates = {path: stem if sum(s.casefold() == stem.casefold() for s in stems) == 1 else path + '.md'
                  for path, stem in zip(paths, stems)}
    outputs, names = {}, set()
    for path in sorted(paths, key=lambda p: (p.casefold(), p)):
        output = candidates[path]
        if output.casefold() in names or sum(c.casefold() == output.casefold() for c in candidates.values()) > 1:
            output = path + '-' + sha256(path.encode()).hexdigest() + '.md'
        while output.casefold() in names:
            output += '.md'
        names.add(output.casefold())
        outputs[path] = output
    items = []
    for path, spec_file, stem in zip(paths, spec.files, stems):
        ext = PurePosixPath(path).suffix.lower()
        output = outputs[path]
        state, code, message = 'waiting', None, None
        if ext not in ('.pdf', '.docx', '.txt', '.doc'):
            state, code, message = 'skipped', 'unsupported_format', '不支持此格式。'
        elif ext == '.doc':
            state, code, message = 'skipped', 'legacy_word', '旧版 .doc 暂不支持，请另存为 .docx。'
        elif not spec_file.size:
            state, code, message = 'failed', 'empty_file', '文件为空。'
        elif spec_file.size > MAX_FILE:
            state, code, message = 'failed', 'file_limit', '文件超过 20 MiB 限制。'
        items.append({'id': uuid4().hex, 'path': path, 'size': spec_file.size, 'format': ext[1:],
            'output': output, 'state': state, 'code': code, 'message': message})
    batch = {'id': uuid4().hex, 'revision': 0, 'root_name': root, 'state': 'uploading', 'created': time.time(),
             'encoding': spec.encoding, 'project_id': spec.project_id, 'files': items}
    runner = request.app.state.conversions
    with runner.lock:
        try:
            runner.directory(batch['id']).mkdir()
            store.create(batch)
        except ValueError as exc:
            runner.directory(batch['id']).rmdir()
            raise HTTPException(409, str(exc)) from None
        except OSError:
            raise HTTPException(507, '磁盘空间不足或无法创建转换目录。') from None
    return store.public(batch)


class LocalBatchSpec(BaseModel):
    project_id: int | None = Field(default=None, gt=0)


@router.post('/clear')
def clear_documents(request: Request, payload: dict):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json' or set(payload) != {'confirm'} or payload.get('confirm') is not True:
        raise HTTPException(422, '请明确确认清理操作。')
    runner = request.app.state.conversions
    with runner.lock:
        if runner.active_batch_id is not None or runner.inflight_uploads:
            raise HTTPException(409, '转换或上传仍在结束，请稍后重试。')
        if not runner.downloads.acquire(blocking=False):
            raise HTTPException(409, '正在下载转换结果，请稍后重试。')
        try:
            from src.persistence.conversion_cleanup import clear
            return clear()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except (OSError, sqlite3.Error):
            raise HTTPException(507, '无法安全清理转换结果，请检查目录和磁盘权限。') from None
        finally:
            runner.downloads.release()


@router.post('/preprocess', status_code=201)
def preprocess_batch(request: Request, payload: dict):
    with request.app.state.conversions.lock:
        return _preprocess_batch(request, payload)


def _preprocess_batch(request: Request, payload: dict):
    from src.infrastructure.settings import preprocesser_config
    from src.literature.conversion.models import MAX_OUTPUT
    from src.persistence.document_classifications import get_classification

    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json' or payload:
        raise HTTPException(415, '请使用空 JSON 对象提交分析请求。')
    runner = request.app.state.conversions
    root = data_directory() / 'documents' / 'conversions'
    config = preprocesser_config()
    model = config['model']
    if not model or not config['base_url']:
        raise HTTPException(422, '请先在通用设置中配置模型服务地址和模型名称。')
    work, unchanged = [], []
    seen, scanned_bytes = 0, 0
    try:
        for component in (root.parent, root):
            if component.is_symlink() or os.path.isjunction(component):
                raise OSError('unsafe document directory')
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != '.batches' and not (Path(current) / d).is_symlink() and not os.path.isjunction(Path(current) / d))
            for name in sorted(files):
                path = Path(current) / name
                if path.suffix.lower() != '.md' or path.is_symlink() or os.path.isjunction(path) or not path.is_file():
                    continue
                seen += 1
                if seen > 10000:
                    raise HTTPException(413, '文档数量超过扫描限制。')
                relative = path.relative_to(root).as_posix()
                try:
                    safe_path(relative)
                    # Recheck each ancestor before opening a candidate.
                    for part in [root, *[root.joinpath(*Path(relative).parts[:i]) for i in range(1, len(Path(relative).parts) + 1)]]:
                        if part.is_symlink() or os.path.isjunction(part): raise OSError('unsafe path')
                    if not path.resolve().is_relative_to(root.resolve()): raise OSError('unsafe path')
                    size = path.stat().st_size
                    item = {'id': uuid4().hex, 'path': relative, 'output': relative,
                            'size': size, 'format': 'md', 'state': 'uploaded', 'code': None, 'message': None}
                    previous = get_classification(relative)
                    if previous and size <= MAX_OUTPUT:
                        scanned_bytes += size
                        if scanned_bytes > 2 * 1024 * 1024 * 1024:
                            raise HTTPException(413, '文档总量超过扫描限制。')
                        with path.open('rb') as source:
                            raw = source.read(MAX_OUTPUT + 1)
                        if sha256(raw).hexdigest() == previous['digest']:
                            item.update(state='skipped', code='already_classified', message='内容未变化，已跳过。',
                                        classification={key: previous[key] for key in ('domains', 'tags', 'description')})
                            if len(unchanged) < MAX_FILES: unchanged.append(item)
                            continue
                    if len(work) < MAX_FILES: work.append(item)
                except (OSError, ValueError, HTTPException) as exc:
                    if isinstance(exc, HTTPException) and exc.status_code == 413: raise
                    if len(work) < MAX_FILES:
                        work.append({'id': uuid4().hex, 'path': relative, 'output': relative, 'size': 0,
                                     'format': 'md', 'state': 'failed', 'code': 'read_failed', 'message': '无法读取文件。'})
        if not seen:
            raise HTTPException(422, '转换目录中没有 Markdown 文档。')
        items = (work + unchanged)[:MAX_FILES]
        batch = {'id': uuid4().hex, 'revision': 0, 'root_name': 'Markdown', 'kind': 'preprocess',
                 'model_name': model, 'state': 'queued', 'created': time.time(), 'files': items}
        with runner.lock:
            store.create(batch)
        runner.wakeup.set()
        return store.public(batch)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError:
        raise HTTPException(507, '无法读取本地文档。') from None


@router.post('/prepare', status_code=201)
def prepare_documents(spec: LocalBatchSpec, request: Request):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, '请使用 JSON 提交预处理请求。')
    from src.infrastructure.settings import preprocesser_config
    config = preprocesser_config()
    if not config['model'] or not config['base_url']:
        raise HTTPException(422, '请先在通用设置中配置模型服务地址和模型名称。')
    if spec.project_id is not None and not repo.project_exists(spec.project_id):
        raise HTTPException(404, '当前项目不存在。')
    return _convert_originals(spec, request, combined_model=config['model'])


@router.post('/open-originals')
def open_originals():
    directory = data_directory() / 'documents' / 'originals'
    try:
        if any(p.is_symlink() or os.path.isjunction(p) for p in (directory.parent, directory)):
            raise OSError('unsafe document directory')
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory.resolve()))
    except (OSError, AttributeError):
        raise HTTPException(500, '无法打开本地论文目录。') from None
    return {'opened': True}


@router.post('/from-originals', status_code=201)
def convert_originals(spec: LocalBatchSpec, request: Request):
    return _convert_originals(spec, request)


def _convert_originals(spec: LocalBatchSpec, request: Request, *, combined_model: str | None = None):
    with request.app.state.conversions.lock:
        return _convert_originals_locked(spec, request, combined_model=combined_model)


def _convert_originals_locked(spec: LocalBatchSpec, request: Request, *, combined_model: str | None = None):
    root = data_directory() / 'documents' / 'originals'
    if any(p.is_symlink() or os.path.isjunction(p) for p in (root.parent, root)):
        raise HTTPException(422, '原始论文目录包含不安全的链接。')
    root.mkdir(parents=True, exist_ok=True)
    # Do not follow directory links or import files outside the fixed directory.
    paths = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(parent) / d).is_symlink() and not os.path.isjunction(Path(parent) / d) and not (Path(parent) == root and d == '.store')]
        for name in files:
            path = Path(parent) / name
            if not path.is_symlink() and not os.path.isjunction(path) and path.is_file() and path.resolve().is_relative_to(root.resolve()): paths.append(path)
    paths.sort(key=lambda p: p.relative_to(root).as_posix().casefold())
    if not paths:
        if not combined_model: raise HTTPException(422, '原始论文目录为空。')
        batch = {'id': uuid4().hex, 'revision': 0, 'root_name': 'originals', 'kind': 'combined', 'model_name': combined_model, 'project_id': spec.project_id, 'state': 'queued', 'created': time.time(), 'files': []}
        try:
            store.create(batch)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        request.app.state.conversions.wakeup.set()
        return store.public(batch)
    try:
        conversions = data_directory() / 'documents' / 'conversions'
        work, rest = [], []
        for path in paths:
            relative = path.relative_to(root)
            output = conversions / (relative.as_posix() + '.md')
            plain = (conversions / relative).with_suffix('.md')
            already = ((output.is_file() and not output.is_symlink()) or
                       (plain.is_file() and not plain.is_symlink()))
            eligible = path.suffix.lower() in ('.pdf', '.docx', '.txt') and not already
            (work if eligible else rest).append(path)
        # Select new work first. Each click can advance the next bounded batch;
        # previously converted entries fill any remaining manifest capacity.
        selected, total = [], 0
        for path in work + rest:
            size = path.stat().st_size
            if len(selected) >= MAX_FILES: break
            if total + size > MAX_BATCH: continue
            selected.append(path)
            total += size
        if not selected:
            raise HTTPException(413, '没有可处理的文件：文件超过单批大小限制。')
        paths = selected
        files = [FileSpec(path=p.relative_to(root).as_posix(), size=p.stat().st_size) for p in paths]
        batch = create_batch(BatchSpec(root_name='originals', files=files, project_id=spec.project_id), request)
        runner = request.app.state.conversions
        def mark_local(b):
            b['local_originals'] = True
            if combined_model:
                b['kind'] = 'combined'
                b['model_name'] = combined_model
            for item in b['files']:
                item['output'] = item['path'] + '.md'
        batch = store.update(batch['id'], mark_local)
        for path, item in zip(paths, batch['files']):
            if item['state'] != 'waiting': continue
            output_path = data_directory() / 'documents' / 'conversions' / Path(item['output'])
            plain_path = (data_directory() / 'documents' / 'conversions' / Path(item['path'])).with_suffix('.md')
            if not output_path.resolve().is_relative_to((data_directory() / 'documents' / 'conversions').resolve()):
                runner._change_item(batch['id'], item['id'], state='failed', code='unsafe_output', message='输出目录包含不安全的链接。')
                continue
            already = (output_path.is_file() and not output_path.is_symlink()) or (plain_path.is_file() and not plain_path.is_symlink())
            retry_import = False
            if already:
                paper_id = None
                if spec.project_id and item['format'] == 'pdf' and item['size'] <= MAX_FILE:
                    try:
                        from apps.api.pdf_import import publish_snapshot, markdown_source_matches
                        digest = sha256()
                        with path.open('rb') as source:
                            for chunk in iter(lambda: source.read(256 * 1024), b''):
                                digest.update(chunk)
                        # An existing Markdown document can only be linked to this PDF
                        # when its converter metadata identifies the same source bytes.
                        existing_md = output_path if output_path.is_file() else plain_path
                        with existing_md.open('r', encoding='utf-8') as md:
                            header = md.read(8192)
                        matches = markdown_source_matches(header, digest.hexdigest())
                        from src.persistence.storage import connect_database
                        with connect_database() as db:
                            exists = repo.existing_version(db, digest.hexdigest())
                        if exists:
                            snapshot = publish_snapshot(path, digest.hexdigest())
                            repo.update_version_path(digest.hexdigest(), snapshot)
                            if matches:
                                paper_id = repo.link_existing_version(spec.project_id, digest.hexdigest())
                        elif matches:
                            retry_import = True
                            expected_digest = digest.hexdigest()
                    except (OSError, UnicodeError):
                        runner._change_item(batch['id'], item['id'], state='failed', code='import_failed', message='原文快照保存失败，请重试。')
                        continue
                if not retry_import:
                    runner._change_item(batch['id'], item['id'], state='skipped', code='already_converted', message='已有同名 Markdown，已跳过。', paper_id=paper_id)
                    continue
            target = runner.directory(batch['id']) / (item['id'] + '.input')
            try:
                digest = sha256()
                size = 0
                with path.open('rb') as source, target.open('xb') as out:
                    while chunk := source.read(256 * 1024):
                        size += len(chunk)
                        if size > item['size'] or size > MAX_FILE: raise ValueError('file changed')
                        digest.update(chunk)
                        out.write(chunk)
                if size != item['size']: raise ValueError('file changed')
                if retry_import and digest.hexdigest() != expected_digest: raise ValueError('file changed')
                runner._change_item(batch['id'], item['id'], state='uploaded', digest=digest.hexdigest(), retry_import=retry_import)
            except (OSError, ValueError):
                target.unlink(missing_ok=True)
                runner._change_item(batch['id'], item['id'], state='failed', code='read_failed', message='无法读取文件或文件已改变。')
        return start_batch(batch['id'], request)
    except OSError:
        raise HTTPException(507, '无法读取或保存本地文件。') from None


@router.post('/{batch_id}/files/{file_id}')
def upload_file(batch_id: str, file_id: str, request: Request, file: UploadFile = File(...)):
    runner = request.app.state.conversions
    with runner.lock:
        batch = batch_for(batch_id)
        item = item_for(batch, file_id)
        if batch.get('kind') == 'preprocess' or batch['state'] != 'uploading' or item['state'] != 'waiting':
            raise HTTPException(409, '此文件不再等待上传。')
        runner._change_item(batch_id, file_id, state='uploading')
        runner.inflight_uploads += 1
    temp = runner.directory(batch_id) / f'{file_id}.upload'
    try:
        size, digest = 0, sha256()
        with temp.open('xb') as target:
            while content := file.file.read(256*1024):
                size += len(content)
                if size > min(MAX_FILE, item['size']): raise HTTPException(413, '实际文件大小超出声明或限制。')
                digest.update(content)
                target.write(content)
        if size != item['size']: raise HTTPException(422, '文件上传不完整。')
        with runner.lock:
            latest = batch_for(batch_id)
            if latest['state'] != 'uploading' or item_for(latest, file_id)['state'] != 'uploading':
                raise HTTPException(409, '批次已取消或已提交。')
            temp.replace(temp.with_suffix('.input'))
            runner._change_item(batch_id, file_id, state='uploaded', digest=digest.hexdigest())
        return {'state': 'uploaded'}
    except BaseException as exc:
        temp.unlink(missing_ok=True)
        with runner.lock:
            current = store.get(batch_id)
            if current and current['state'] == 'uploading':
                runner._change_item(batch_id, file_id, state='failed', code='upload_failed', message='上传中断或文件写入失败，请重新选择文件重试。')
        if isinstance(exc, OSError): raise HTTPException(507, '磁盘空间不足或文件写入失败。') from None
        raise
    finally:
        with runner.lock:
            runner.inflight_uploads -= 1
        file.file.close()


@router.post('/{batch_id}/start')
def start_batch(batch_id: str, request: Request):
    runner = request.app.state.conversions
    with runner.lock:
        batch = batch_for(batch_id)
        if batch.get('kind') == 'preprocess': raise HTTPException(409, '分析批次无需上传或启动。')
        if batch['state'] != 'uploading': return store.public(batch)
        def start(b):
            b['state'] = 'queued'
            for item in b['files']:
                if item['state'] in ('waiting', 'uploading'):
                    item.update(state='failed', code='upload_missing', message='文件未完整上传。')
        batch = store.update(batch_id, start)
    runner.wakeup.set()
    return store.public(batch)


@router.get('/{batch_id}')
def get_batch(batch_id: str): return store.public(batch_for(batch_id))


@router.post('/{batch_id}/cancel')
def cancel_batch(batch_id: str, request: Request):
    runner = request.app.state.conversions
    with runner.lock:
        batch = batch_for(batch_id)
        was_running = batch['state'] == 'running'
        if batch['state'] not in store.TERMINAL:
            batch = store.update(batch_id, lambda b: store.end(b, 'cancelled', 'user_cancelled', '用户已取消。'))
        if not was_running:
            runner.clean_inputs(batch_id, uploads=False)
        # The runner owns active inputs; uploads clean their own temporary file.
    runner.wakeup.set()
    return store.public(batch)


def artifact(request, batch_id, file_id):
    batch = batch_for(batch_id)
    if batch.get('kind') == 'preprocess': raise HTTPException(409, '分析批次不提供转换文件。')
    item = item_for(batch, file_id)
    if item['state'] not in ('succeeded', 'partial'): raise HTTPException(409, '此文件尚无可用 Markdown。')
    path = request.app.state.conversions.directory(batch_id) / f'{file_id}.md'
    if not path.is_file(): raise HTTPException(410, '转换结果已不存在，请重新转换。')
    return batch, item, path


@router.get('/{batch_id}/files/{file_id}/preview')
def preview(batch_id: str, file_id: str, request: Request):
    with request.app.state.conversions.lock:
        batch, item, path = artifact(request, batch_id, file_id)
        with path.open(encoding='utf-8') as source:
            text = source.read(64001)
        metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    return {'markdown': text[:64000], 'truncated': len(text) > 64000,
            'mapping': metadata.get('mapping', []), 'warnings': metadata.get('warnings', [])}


@router.get('/{batch_id}/files/{file_id}/markdown')
def markdown(batch_id: str, file_id: str, request: Request):
    runner = request.app.state.conversions
    with runner.lock:
        if not runner.downloads.acquire(blocking=False): raise HTTPException(409, '正在下载其他结果，请稍后重试。')
        try:
            _, item, path = artifact(request, batch_id, file_id)
            return ConversionDownload(path, media_type='text/markdown; charset=utf-8',
                                      filename=PurePosixPath(item['output']).name, release=runner.downloads.release)
        except BaseException:
            runner.downloads.release()
            raise


@router.get('/{batch_id}/zip')
def archive(batch_id: str, request: Request):
    runner = request.app.state.conversions
    with runner.lock:
        if not runner.downloads.acquire(blocking=False): raise HTTPException(409, '正在下载其他结果，请稍后重试。')
        try:
            batch = batch_for(batch_id)
        except BaseException:
            runner.downloads.release()
            raise
    if batch.get('kind') == 'preprocess':
        runner.downloads.release()
        raise HTTPException(409, '分析批次不提供转换文件。')
    if batch['state'] not in store.TERMINAL:
        runner.downloads.release()
        raise HTTPException(409, '请等待批次结束后下载 ZIP。')
    ready = [f for f in batch['files'] if f['state'] in ('succeeded', 'partial') and f['format'] != 'md']
    if not ready:
        runner.downloads.release()
        raise HTTPException(409, '此批次没有可下载的结果。')
    directory = runner.directory(batch_id)
    output = directory / f'{uuid4().hex}.zip'
    try:
        with ZipFile(output, 'x', compression=ZIP_DEFLATED) as zipped:
            for item in ready:
                _, _, path = artifact(request, batch_id, item['id'])
                base = batch['root_name'] + '-Markdown/' + item['output']
                zipped.write(path, base)
                metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
                # Include positioning and limitations, never duplicate the extracted original text.
                metadata.pop('pages', None)
                zipped.writestr(base + '.metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))
            zipped.writestr(batch['root_name']+'-Markdown/conversion-manifest.json', json.dumps(store.public(batch), ensure_ascii=False, indent=2))
    except BaseException:
        try: output.unlink(missing_ok=True)
        finally: runner.downloads.release()
        raise
    return ConversionDownload(output, media_type='application/zip', filename=batch['root_name']+'-Markdown.zip',
                              release=runner.downloads.release, temporary=True)
