"""Local bounded diagnostic logs; never emit exception messages or request bodies."""
import logging
import errno
from contextlib import contextmanager
import os
import re
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from src.persistence.storage import data_directory

_secrets = ()
_process_file_lock = threading.RLock()
_APPLICATION_LOG = re.compile(r'(?:api|worker)\.log(?:\.\d+)?\Z')
_LOG_LOCK_TIMEOUT = 10


def _safe_file_log_failure():
    try:
        sys.stderr.write('Tufa Scholar: local file logging failed\n')
    except Exception:
        pass


@contextmanager
def _log_rotation_lock(directory):
    """Serialize log writes, rotation, and clearing across API/worker processes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not _process_file_lock.acquire(timeout=_LOG_LOCK_TIMEOUT):
        raise TimeoutError('log lock timed out') from None
    try:
        with (directory / '.tufa-logs.lock').open('a+b') as lock_file:
            deadline = time.monotonic() + _LOG_LOCK_TIMEOUT
            if os.name == 'nt':
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b'\0')
                    lock_file.flush()
                while True:
                    lock_file.seek(0)
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        if time.monotonic() >= deadline:
                            raise TimeoutError('log lock timed out') from None
                        time.sleep(0.025)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('log lock timed out') from None
                        time.sleep(0.025)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        _process_file_lock.release()


class _LockedRotatingFileHandler(RotatingFileHandler):
    """Open the active file only while holding the shared rotation lock."""

    def __init__(self, filename, *, maxBytes, backupCount, encoding):
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding, delay=True)

    def emit(self, record):
        try:
            with _log_rotation_lock(Path(self.baseFilename).parent):
                try:
                    super().emit(record)
                finally:
                    if self.stream is not None:
                        try:
                            self.flush()
                        finally:
                            try:
                                self.stream.close()
                            finally:
                                self.stream = None
        except Exception:
            _safe_file_log_failure()

    def handleError(self, record):
        # Avoid logging's default traceback, which can include unsafe record data.
        _safe_file_log_failure()


def refresh_secrets():
    global _secrets
    # Called at startup and after a settings update, never during formatting.
    from src.infrastructure.settings import ENV, SECRET_FIELDS, _local
    local = _local()
    values = [value for name in SECRET_FIELDS for value in (os.environ.get(ENV[name]), local.get(name))]
    _secrets = tuple(sorted(set(_secrets) | {v for v in values if v}, key=len, reverse=True))


class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Traceback positions are useful, but exception text may contain credentials.
        if record.exc_info:
            record.exc_info = None
            record.exc_text = None
        rendered = super().format(record)
        rendered = re.sub(r'https?://[^\s]+', '[url]', rendered, flags=re.I)
        for value in _secrets:
            rendered = rendered.replace(value, '[redacted]')
        return rendered


def safe_stack(exc):
    frames = traceback.extract_tb(exc.__traceback__)
    return ' > '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in frames)


def configure_logging(component, max_bytes=5 * 1024 * 1024, backup_count=3):
    refresh_secrets()
    directory = data_directory() / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('tufa')
    for handler in logger.handlers[:]:
        logger.removeHandler(handler); handler.close()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = SafeFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
    file_handler = _LockedRotatingFileHandler(directory / f'{component}.log', maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter); logger.addHandler(handler)
    for name in ('src.integrations', 'src.literature.retrieval'):
        child = logging.getLogger(name)
        child.handlers.clear()
        child.setLevel(logging.INFO)
        child.propagate = False
        for handler in logger.handlers:
            child.addHandler(handler)
    # Default access logging includes full query strings.
    logging.getLogger('uvicorn.access').disabled = True
    for name in ('httpx','httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)


def _validated_log_directory(data_root):
    directory = data_root / 'logs'
    if directory.is_symlink():
        raise OSError('log directory is not a regular directory')
    if not directory.exists():
        return None
    if not directory.is_dir():
        raise OSError('log directory is not a regular directory')
    try:
        directory.resolve().relative_to(data_root)
    except (OSError, ValueError):
        raise OSError('log directory is outside the data directory') from None
    return directory


def clear_application_logs():
    """Remove only API/worker active logs and numeric rotation backups."""
    data_root = data_directory().resolve()
    directory = _validated_log_directory(data_root)
    if directory is None:
        return 0

    removed = 0
    with _log_rotation_lock(directory):
        # Recheck after acquiring the interprocess lock before enumerating paths.
        directory = _validated_log_directory(data_root)
        if directory is None:
            return 0
        for path in directory.iterdir():
            if not _APPLICATION_LOG.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            path.unlink()
            removed += 1
    return removed


def close_logging():
    for name in ('src.integrations', 'src.literature.retrieval'):
        logging.getLogger(name).handlers.clear()
    logger = logging.getLogger('tufa')
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
