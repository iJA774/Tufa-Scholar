"""Bounded direct PDF parsing and atomic, content-addressed originals."""
import json
import multiprocessing
import os
import tempfile
import threading
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException
from src.literature.conversion.models import MAX_FILE, MAX_OUTPUT, MAX_PDF_PAGES, PARSE_TIMEOUT
from src.persistence.storage import data_directory

CHUNK = 256 * 1024
_upload_slot = threading.BoundedSemaphore(1)
_snapshot_lock = threading.Lock()


def markdown_source_matches(header, digest):
    """Read only a bounded, complete converter front matter block."""
    lines = header[:8192].splitlines()
    if not lines or lines[0].strip() != '---':
        return False
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            return False
        key, separator, value = line.partition(':')
        if separator and key == 'source_sha256':
            try:
                matched = json.loads(value.strip()) == digest
            except ValueError:
                return False
            # The closing boundary must be present within the bounded prefix.
            return matched and any(item.strip() == '---' for item in lines[index + 1:])
    return False


def publish_snapshot(source, digest):
    # API uploads and the conversion coordinator share this process.
    with _snapshot_lock:
        return _publish_snapshot(source, digest)


def _publish_snapshot(source, digest):
    root = data_directory() / 'documents' / 'originals' / '.store'
    if any(p.is_symlink() or os.path.isjunction(p) for p in (root.parent.parent, root.parent, root)):
        raise OSError('unsafe original storage')
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f'{digest}.pdf'
    # A verified existing snapshot need not be replaced (and may be open for download).
    if not destination.is_symlink() and destination.is_file():
        existing = sha256()
        with destination.open('rb') as saved:
            for chunk in iter(lambda: saved.read(CHUNK), b''):
                existing.update(chunk)
        if existing.hexdigest() == digest:
            return destination
    # A unique same-directory staging file keeps incomplete copies invisible.
    fd, name = tempfile.mkstemp(prefix='.pdf-', suffix='.tmp', dir=root)
    temporary = Path(name)
    try:
        actual = sha256()
        with os.fdopen(fd, 'wb') as target, Path(source).open('rb') as incoming:
            for chunk in iter(lambda: incoming.read(CHUNK), b''):
                actual.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if actual.hexdigest() != digest:
            raise OSError('original digest changed')
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _parse_pdf(source, output):
    """Spawn target: never send parser errors or file contents through logs."""
    try:
        import logging
        import warnings
        logging.disable(logging.CRITICAL)
        warnings.simplefilter('ignore')
        from src.literature.conversion.resources import limit_memory
        limit_memory()
        from pypdf import PdfReader
        reader = PdfReader(source)
        if len(reader.pages) > MAX_PDF_PAGES:
            result = {'error': 'PDF 超过单文件 200 页限制。'}
        else:
            pages = []
            size = 0
            for page in reader.pages:
                text = (page.extract_text() or '').strip()
                size += len(text.encode('utf-8'))
                if size > MAX_OUTPUT:
                    raise ValueError('output limit')
                pages.append(text)
            result = {'pages': pages, 'title': str(reader.metadata.title or '').strip() if reader.metadata else ''}
    except Exception:
        result = {'error': '无法解析 PDF 文件'}
    try:
        Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    except (OSError, MemoryError):
        pass


def parse_upload(file, title=None):
    """Copy in chunks and run a single killable parser in a threadpool worker."""
    if not _upload_slot.acquire(blocking=False):
        raise HTTPException(503, 'PDF 解析繁忙，请稍后重试。')
    try:
        with tempfile.TemporaryDirectory(prefix='tufa-pdf-') as directory:
            source, output = Path(directory) / 'input.pdf', Path(directory) / 'result.json'
            digest = sha256()
            size = 0
            with source.open('wb') as target:
                while chunk := file.file.read(CHUNK):
                    size += len(chunk)
                    if size > MAX_FILE:
                        raise HTTPException(413, 'PDF 超过单文件 20 MiB 限制。')
                    if size == len(chunk) and not chunk.startswith(b'%PDF-'):
                        raise HTTPException(422, '请上传有效的 PDF 文件')
                    digest.update(chunk)
                    target.write(chunk)
            if not size:
                raise HTTPException(422, '请上传有效的 PDF 文件')
            process = multiprocessing.get_context('spawn').Process(target=_parse_pdf, args=(str(source), str(output)), daemon=True)
            try:
                process.start()
                process.join(PARSE_TIMEOUT)
                if process.is_alive():
                    process.terminate()
                    process.join()
                    raise HTTPException(422, 'PDF 解析超过时限（45 秒）。')
                if not output.is_file():
                    raise HTTPException(422, '无法解析 PDF 文件')
                try:
                    result = json.loads(output.read_text(encoding='utf-8'))
                except (OSError, UnicodeError, ValueError):
                    raise HTTPException(422, '无法解析 PDF 文件') from None
                if 'error' in result:
                    raise HTTPException(422, result['error'])
                if not (title or result['title'] or Path(file.filename or '文献.pdf').stem).strip():
                    raise HTTPException(422, '文献标题不能为空')
                path = publish_snapshot(source, digest.hexdigest())
                return digest.hexdigest(), path, result
            finally:
                if process.pid:
                    if process.is_alive():
                        process.terminate()
                        process.join()
                    process.close()
    finally:
        _upload_slot.release()
