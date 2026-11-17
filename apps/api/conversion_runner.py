"""One bounded offline converter, separate from the single research worker."""
import json
from hashlib import sha256
import logging
import multiprocessing
import os
from uuid import uuid4
import shutil
import threading
import time
from pathlib import Path
from src.literature.conversion.converter import process_file
from src.persistence import conversions as store, repository as repo
from src.persistence.storage import data_directory


class ConversionRunner:
    def __init__(self, timeout=45):
        documents = data_directory() / 'documents'
        conversions = documents / 'conversions'
        for path in (documents, conversions):
            if path.is_symlink() or os.path.isjunction(path):
                raise OSError('unsafe document directory')
        self.root = conversions / '.batches'
        if self.root.is_symlink() or os.path.isjunction(self.root):
            raise OSError('unsafe batch directory')
        self.legacy_root = data_directory() / 'conversions'
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.thread = None
        self.active_batch_id = None
        self.inflight_uploads = 0
        self.downloads = threading.BoundedSemaphore(1)

    def directory(self, batch_id):
        # IDs come exclusively from persisted, server-generated manifests.
        legacy = self.legacy_root / batch_id
        return legacy if legacy.is_dir() else self.root / batch_id

    def start(self):
        for batch in store.active():
            store.update(batch['id'], lambda b: store.end(b, 'interrupted', 'service_interrupted', '服务重启，转换已中断；请重新选择文件。'))
            self.clean_inputs(batch['id'])
        self.thread = threading.Thread(target=self.run, name='document-converter', daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=8)
        with self.lock:
            for batch in store.active():
                store.update(batch['id'], lambda b: store.end(b, 'interrupted', 'service_interrupted', '服务关闭，转换已中断。'))
                self.clean_inputs(batch['id'])

    def clean_inputs(self, batch_id, *, uploads=True):
        directory = self.directory(batch_id)
        if directory.is_dir():
            for pattern in (('*.input', '*.upload') if uploads else ('*.input',)):
                for path in directory.glob(pattern):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logging.getLogger('tufa.api').warning('conversion_cleanup_deferred')

    def run(self):
        while not self.stopping.is_set():
            try:
                for batch in store.active():
                    if self.stopping.is_set(): break
                    if batch['state'] == 'uploading' and time.time() - batch.get('updated', batch['created']) > 15*60:
                        with self.lock:
                            store.update(batch['id'], lambda b: store.end(b, 'interrupted', 'upload_expired', '上传未完成，批次已过期。'))
                            self.clean_inputs(batch['id'])
                    elif batch['state'] == 'queued':
                        self.convert_batch(batch['id'])
                    elif batch['state'] == 'running':
                        # No file is active outside convert_batch; recover a prior storage failure.
                        store.update(batch['id'], lambda b: store.end(b, 'interrupted', 'coordinator_error', '转换协调异常，批次已中断。'))
                        self.clean_inputs(batch['id'])
            except Exception as exc:
                # No document data or raw parser errors enter application logs.
                # A claimed batch is finalized by convert_batch's own exception boundary.
                logging.getLogger('tufa.api').error('conversion_coordinator_error type=%s', type(exc).__name__)
            self.wakeup.wait(.5)
            self.wakeup.clear()

    def _change_item(self, batch_id, file_id, **values):
        def change(batch):
            for item in batch['files']:
                if item['id'] == file_id: item.update(values)
        return store.update(batch_id, change)

    def convert_batch(self, batch_id):
        claimed = False
        try:
            with self.lock:
                queued = store.get(batch_id)
                if not queued or queued['state'] != 'queued': return
                store.update(batch_id, lambda b: b.update(state='running'))
                self.active_batch_id = batch_id
                claimed = True
            for item in store.get(batch_id)['files']:
                batch = store.get(batch_id)
                if batch['state'] == 'cancelled' or self.stopping.is_set(): break
                if item['state'] != 'uploaded': continue
                if batch.get('kind') == 'preprocess':
                    self.preprocess_item(batch, item)
                else:
                    self.convert_item(batch, item)
            if store.get(batch_id).get('kind') == 'combined' and store.get(batch_id)['state'] == 'running' and not self.stopping.is_set():
                self.classify_converted(batch_id)
            with self.lock:
                current = store.get(batch_id)
                if current['state'] != 'cancelled':
                    if self.stopping.is_set():
                        store.update(batch_id, lambda b: store.end(b, 'interrupted', 'service_interrupted', '服务关闭，转换已中断。'))
                    else:
                        store.update(batch_id, lambda b: b.update(state='completed'))
        except Exception:
            with self.lock:
                if store.get(batch_id)['state'] not in store.TERMINAL:
                    store.update(batch_id, lambda b: store.end(b, 'interrupted', 'storage_error', '转换执行或存储失败，请检查磁盘空间。'))
        finally:
            if claimed:
                with self.lock:
                    try:
                        self.clean_inputs(batch_id)
                    finally:
                        self.active_batch_id = None

    def classify_converted(self, batch_id):
        from src.literature.conversion.models import MAX_OUTPUT, MAX_FILES
        from src.persistence.document_classifications import get_classification
        root = data_directory() / 'documents' / 'conversions'
        from src.persistence.markdown_corpus import is_indexed
        pending, backfill, known = [], [], []
        project_id = store.get(batch_id).get('project_id')
        seen = 0
        if any(p.is_symlink() or os.path.isjunction(p) for p in (root.parent, root)):
            raise OSError('unsafe document directory')
        for current, dirs, files in os.walk(root, followlinks=False):
            if self.stopping.is_set() or store.get(batch_id)['state'] != 'running': return
            current_path = Path(current)
            relative_dir = current_path.relative_to(root)
            if any(p.is_symlink() or os.path.isjunction(p) for p in [root.parent, root, *[root.joinpath(*relative_dir.parts[:i]) for i in range(1, len(relative_dir.parts) + 1)]]):
                raise OSError('unsafe document directory')
            dirs[:] = sorted(d for d in dirs if d != '.batches' and not (Path(current) / d).is_symlink() and not os.path.isjunction(Path(current) / d))
            for name in sorted(files):
                seen += 1
                if seen % 32 == 0 and (self.stopping.is_set() or store.get(batch_id)['state'] != 'running'):
                    return
                if any(p.is_symlink() or os.path.isjunction(p) for p in [root.parent, root, *[root.joinpath(*relative_dir.parts[:i]) for i in range(1, len(relative_dir.parts) + 1)]]):
                    raise OSError('unsafe document directory')
                path = Path(current) / name
                if path.suffix.lower() != '.md' or path.is_symlink() or os.path.isjunction(path) or not path.is_file(): continue
                relative = path.relative_to(root).as_posix()
                try:
                    size = path.stat().st_size
                    previous = get_classification(relative)
                    unchanged = False
                    if previous and size <= MAX_OUTPUT:
                        with path.open('rb') as source:
                            unchanged = sha256(source.read(MAX_OUTPUT + 1)).hexdigest() == previous['digest']
                    item = {'id': uuid4().hex, 'path': relative, 'output': relative, 'size': size, 'format': 'md', 'state': 'uploaded', 'code': None, 'message': None}
                    if not unchanged:
                        if len(pending) < MAX_FILES: pending.append(item)
                    elif project_id and not is_indexed(project_id, relative, previous['digest']):
                        if len(backfill) < MAX_FILES: backfill.append(item)
                    else:
                        if len(known) < MAX_FILES: known.append(item)
                except OSError:
                    continue
        items = (pending + backfill + known)[:MAX_FILES]
        if not items and not store.get(batch_id)['files']:
            items = [{'id': uuid4().hex, 'path': '', 'output': '', 'size': 0, 'format': 'md', 'state': 'failed', 'code': 'empty', 'message': '没有可处理的文档。'}]
        with self.lock:
            if store.get(batch_id)['state'] != 'running': return
            store.update(batch_id, lambda b: b['files'].extend(items))
        for item in items:
            batch = store.get(batch_id)
            if batch['state'] != 'running' or self.stopping.is_set(): break
            if item['state'] == 'uploaded': self.preprocess_item(batch, item)

    def preprocess_item(self, batch, item):
        from src.agents.preprocesser import classify_markdown
        from src.integrations.model import ModelServiceError, StructuredOutputError
        from src.persistence.document_classifications import get_classification_by_digest, upsert_classification
        from src.literature.conversion.models import MAX_OUTPUT

        batch_id, file_id = batch['id'], item['id']
        with self.lock:
            if store.get(batch_id)['state'] != 'running': return
            self._change_item(batch_id, file_id, state='running')
        log = logging.getLogger('tufa.api')
        started = time.monotonic()
        stage = 'read'
        log.info('preprocess_item started batch_id=%s file_id=%s', batch_id, file_id)
        try:
            root = data_directory() / 'documents' / 'conversions'
            path = root / Path(item['path'])
            for part in [root.parent, root, *[root.joinpath(*Path(item['path']).parts[:i]) for i in range(1, len(Path(item['path']).parts) + 1)]]:
                if part.is_symlink() or os.path.isjunction(part): raise OSError('unsafe path')
            if not path.resolve().is_relative_to(root.resolve()): raise OSError('unsafe path')
            with path.open('rb') as source:
                raw = source.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT: raise ValueError('file too large')
            digest = sha256(raw).hexdigest()
            markdown = raw.decode('utf-8-sig')
            if not markdown.strip(): raise ValueError('empty document')
            stage = 'lookup'
            previous = get_classification_by_digest(digest)
            if previous:
                classification = {key: previous[key] for key in ('domains', 'tags', 'description')}
                with self.lock:
                    if store.get(batch_id)['state'] == 'running' and not self.stopping.is_set():
                        stage = 'index'
                        upsert_classification(item['path'], digest, classification)
                        self.index_markdown(batch, item, digest, markdown)
                        self._change_item(batch_id, file_id, state='skipped', code='already_classified',
                                          message='相同内容已有分类，已复用。', classification=classification)
                        log.info('preprocess_item reused batch_id=%s file_id=%s elapsed_ms=%.1f',
                                 batch_id, file_id, (time.monotonic() - started) * 1000)
                return
            stage = 'classify'
            result = classify_markdown(markdown, batch['model_name'])
            with self.lock:
                if store.get(batch_id)['state'] != 'running' or self.stopping.is_set(): return
                stage = 'index'
                classification = result.model_dump()
                upsert_classification(item['path'], digest, classification)
                self.index_markdown(batch, item, digest, markdown)
                self._change_item(batch_id, file_id, state='succeeded', classification=classification)
            log.info('preprocess_item completed batch_id=%s file_id=%s elapsed_ms=%.1f',
                     batch_id, file_id, (time.monotonic() - started) * 1000)
        except Exception as exc:
            code, message = {
                'read': ('preprocess_read_failed', '无法读取 Markdown，或文件为空、编码无效、超过大小限制。'),
                'lookup': ('preprocess_lookup_failed', '无法查询已有分类，请检查本机数据库。'),
                'classify': ('preprocess_failed', '文档分析失败，请检查分类模型设置后重试。'),
                'index': ('preprocess_index_failed', '分类结果或索引写入失败，请检查本机数据库后重试。'),
            }[stage]
            if isinstance(exc, StructuredOutputError):
                code, message = 'preprocess_invalid_output', '分类模型仍未返回有效的研究领域、标签和短描述，请重试或更换分类模型。'
            elif isinstance(exc, ModelServiceError):
                code, message = 'preprocess_model_service', '分类模型服务请求失败，请检查模型配置或服务状态后重试。'
            log.warning('preprocess_item failed batch_id=%s file_id=%s stage=%s code=%s error_type=%s elapsed_ms=%.1f',
                        batch_id, file_id, stage, code, type(exc).__name__, (time.monotonic() - started) * 1000)
            with self.lock:
                if store.get(batch_id)['state'] == 'running':
                    self._change_item(batch_id, file_id, state='failed', code=code, message=message)

    def index_markdown(self, batch, item, digest, markdown):
        if batch.get('kind') != 'combined' or not batch.get('project_id'): return
        from src.persistence.markdown_corpus import index_document
        paper_id = None
        for candidate in store.get(batch['id'])['files']:
            if candidate.get('format') == 'pdf' and item['path'] in (candidate['output'], str(Path(candidate['path']).with_suffix('.md')).replace('\\', '/')):
                paper_id = candidate.get('paper_id')
                break
        if paper_id is None:
            rel = Path(item['path'])
            possible = [Path(str(rel)[:-3]), rel.with_suffix('.pdf')]
            root = data_directory() / 'documents' / 'originals'
            for candidate in possible:
                if candidate.suffix.lower() != '.pdf': continue
                path = root / candidate
                try:
                    if any(part.is_symlink() or os.path.isjunction(part) for part in [root.parent, root, *[root.joinpath(*candidate.parts[:i]) for i in range(1, len(candidate.parts) + 1)]]): continue
                    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()): continue
                    with path.open('rb') as source:
                        source_digest = sha256()
                        for chunk in iter(lambda: source.read(256 * 1024), b''): source_digest.update(chunk)
                    paper_id = repo.link_existing_version(batch['project_id'], source_digest.hexdigest())
                    if paper_id: break
                except OSError:
                    continue
        index_document(batch['project_id'], item['path'], digest, markdown, paper_id=paper_id)

    def convert_item(self, batch, item):
        batch_id, file_id = batch['id'], item['id']
        directory = self.directory(batch_id)
        source, output = directory / f'{file_id}.input', directory / f'{file_id}.md'
        if batch.get('local_originals'):
            canonical = data_directory() / 'documents' / 'conversions' / Path(item['output'])
            plain = (data_directory() / 'documents' / 'conversions' / Path(item['path'])).with_suffix('.md')
            if canonical.is_file() or plain.is_file():
                self._change_item(batch_id, file_id, state='skipped', code='already_converted', message='已有同名 Markdown，已跳过。')
                source.unlink(missing_ok=True)
                return
        with self.lock:
            if store.get(batch_id)['state'] != 'running': return
            self._change_item(batch_id, file_id, state='running')
        started = time.monotonic()
        process = multiprocessing.get_context('spawn').Process(target=process_file,
            args=(str(source), str(output), item['path'], batch['encoding']), daemon=True)
        try:
            process.start()
            while process.is_alive():
                if self.stopping.is_set() or store.get(batch_id)['state'] == 'cancelled' or time.monotonic()-started > self.timeout:
                    process.terminate()
                    break
                process.join(.1)
            process.join(timeout=3)
            with self.lock:
                if store.get(batch_id)['state'] == 'cancelled' or self.stopping.is_set():
                    output.unlink(missing_ok=True)
                    output.with_suffix('.json').unlink(missing_ok=True)
                    return
                elapsed = round(time.monotonic()-started, 3)
                if elapsed > self.timeout:
                    self._change_item(batch_id, file_id, state='failed', code='conversion_timeout', message='单文件转换超过时限（45 秒）。', elapsed=elapsed)
                    return
                sidecar = output.with_suffix('.json')
                if not sidecar.exists():
                    self._change_item(batch_id, file_id, state='failed', code='converter_exit', message='转换进程异常退出或磁盘写入失败。', elapsed=elapsed)
                    return
                result = json.loads(sidecar.read_text(encoding='utf-8'))
                if not result['ok']:
                    self._change_item(batch_id, file_id, state='skipped' if result.get('skipped') else 'failed', code=result['code'], message=result['message'], elapsed=elapsed)
                    return
                if batch.get('local_originals'):
                    destination = data_directory() / 'documents' / 'conversions' / Path(item['output'])
                    if not destination.resolve().is_relative_to((data_directory() / 'documents' / 'conversions').resolve()):
                        raise OSError('unsafe output directory')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name('.' + uuid4().hex + '.tmp')
                    try:
                        shutil.copyfile(output, temporary)
                        try:
                            os.link(temporary, destination)
                        except FileExistsError:
                            pass
                    finally:
                        temporary.unlink(missing_ok=True)
                paper_id, import_error = None, None
                if batch.get('project_id') and item['format'] == 'pdf':
                    try:
                        paper_id = self.import_pdf(batch['project_id'], item, source, result)
                    except Exception:
                        import_error = 'Markdown 已生成，项目导入失败；请检查项目是否存在及磁盘空间。'
                self._change_item(batch_id, file_id, state='partial' if result['partial'] else 'succeeded',
                    warnings=result['warnings'], encoding=result['encoding'], digest=result['digest'],
                    paper_id=paper_id, import_error=import_error, elapsed=elapsed)
        finally:
            if process.pid:
                if process.is_alive(): process.terminate()
                process.join(timeout=3)
                process.close()
            source.unlink(missing_ok=True)
            latest = store.get(batch_id)
            final = next(f for f in latest['files'] if f['id'] == file_id)
            if final['state'] not in ('succeeded', 'partial'):
                output.unlink(missing_ok=True)
                output.with_suffix('.json').unlink(missing_ok=True)

    def import_pdf(self, project_id, item, source, result):
        if not repo.project_exists(project_id): raise ValueError('project missing')
        original = data_directory() / 'documents' / 'originals' / '.store' / f'{result["digest"]}.pdf'
        if original.parent.is_symlink() or os.path.isjunction(original.parent):
            raise OSError('unsafe original storage')
        original.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create never overwrites an existing original.
        created = False
        try:
            with original.open('xb') as target:
                created = True
                with source.open('rb') as incoming:
                    shutil.copyfileobj(incoming, target)
        except FileExistsError:
            pass
        except OSError:
            if created: original.unlink(missing_ok=True)
            raise
        paper = repo.save_paper(project_id, {'title': result['title'], 'source': 'upload'}, {
            'filename': Path(item['path']).name, 'digest': result['digest'], 'path': str(original),
            'text': '\n\n'.join(result['pages']), 'pages': result['pages'], 'text_status': 'text_available'})
        return paper['id']
