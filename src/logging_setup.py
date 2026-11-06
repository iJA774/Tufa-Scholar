"""Local bounded diagnostic logs; never emit exception messages or request bodies."""
import logging
import os
import re
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from src.storage import data_directory

_secrets = ()


def refresh_secrets():
    global _secrets
    # Called at startup and after a settings update, never during formatting.
    from src.settings import ENV, SECRET_FIELDS, _local
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
    file_handler = RotatingFileHandler(directory / f'{component}.log', maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
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


def close_logging():
    for name in ('src.integrations', 'src.literature.retrieval'):
        logging.getLogger(name).handlers.clear()
    logger = logging.getLogger('tufa')
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
