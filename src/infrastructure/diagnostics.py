"""Private, credential-redacted failure artifacts for offline diagnosis."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import re
import uuid
import platform
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError

from src.persistence.storage import data_directory

logger = logging.getLogger('tufa.diagnostics')
_scope = ContextVar('diagnostic_scope', default={})


def diagnostic_context():
    """Return a copy of the current task-scoped diagnostic tags."""
    return dict(_scope.get())


@contextmanager
def diagnostic_scope(**tags):
    token = _scope.set({**_scope.get(), **tags})
    try:
        yield
    finally:
        _scope.reset(token)


_SENSITIVE = {'api_key', 'apikey', 'authorization', 'proxy_authorization', 'password', 'secret', 'token', 'headers', 'base_url'}


_URL_CREDENTIALS = re.compile(r'(https?://)[^/\s<>"@]+@', re.I)
_URL_QUERY = re.compile(r'(https?://[^\s?"<>]+)\?[^\s"<>]+', re.I)
_MODEL_THOUGHT = re.compile(r'<think\b[^>]*>.*?(?:</think\s*>|$)', re.I | re.S)


def redact_diagnostic(value, *, secrets=(), strip_thought=False):
    """Remove configured credentials and sensitive fields before saving research data."""
    from src.infrastructure.settings import ENV, SECRET_FIELDS, _local

    known = set(secrets)
    known.update(os.environ.get(ENV[name], '') for name in SECRET_FIELDS)
    try:
        local = _local()
        known.update(local.get(name, '') for name in SECRET_FIELDS)
    except Exception:
        pass
    known = sorted((item for item in known if isinstance(item, str) and item), key=len, reverse=True)

    def clean(item):
        if isinstance(item, dict):
            result = {}
            for key, field in item.items():
                name = str(key).lower().replace('-', '_')
                if name not in _SENSITIVE and not name.endswith(('_api_key', '_secret', '_token', '_password')):
                    result[clean(str(key))] = clean(field)
            return result
        if isinstance(item, (list, tuple)):
            return [clean(field) for field in item]
        if isinstance(item, str):
            if item.lstrip().startswith(('{', '[')):
                try:
                    parsed = json.loads(item)
                except (ValueError, RecursionError):
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    redacted = clean(parsed)
                    if redacted != parsed:
                        return json.dumps(redacted, ensure_ascii=False)
                    return item
            if strip_thought:
                item = _MODEL_THOUGHT.sub('', item)
            item = _URL_CREDENTIALS.sub(r'\1[redacted]@', item)
            item = _URL_QUERY.sub(r'\1?[redacted]', item)
            for secret in known:
                item = item.replace(secret, '[redacted]')
            return item
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return clean(str(item))

    return clean(value)


def record_failure(*, kind, payload, schema, response=None, errors=None, context=None, secrets=()):
    if _scope.get().get("suppress_artifacts"):
        return ""
    try:
        identifier = uuid.uuid4().hex
        directory = data_directory() / 'diagnostics'
        directory.mkdir(parents=True, exist_ok=True)
        def package_version(name):
            try:
                return version(name)
            except PackageNotFoundError:
                return 'unknown'

        artifact = redact_diagnostic({'format_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                          'environment': {'python': platform.python_version(), 'pydantic': package_version('pydantic'), 'langgraph': package_version('langgraph')},
                          'redacted': True, 'kind': kind, 'payload': payload, 'schema': schema, 'response': response,
                          'errors': errors, 'context': {**_scope.get(), **(context or {})}}, secrets=secrets)
        with (directory / (identifier + '.json')).open('x', encoding='utf-8') as output:
            json.dump(artifact, output, ensure_ascii=False, indent=2)
        return identifier
    except Exception as exc:
        logger.warning('diagnostic_write_failed error_type=%s', type(exc).__name__)
        return ''
