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


@contextmanager
def diagnostic_scope(**tags):
    token = _scope.set({**_scope.get(), **tags})
    try:
        yield
    finally:
        _scope.reset(token)


_SENSITIVE = {'api_key', 'apikey', 'authorization', 'proxy_authorization', 'password', 'secret', 'token', 'headers', 'base_url'}


def record_failure(*, kind, payload, schema, response=None, errors=None, context=None, secrets=()):
    try:
        from src.infrastructure.settings import ENV, SECRET_FIELDS, _local
        known = set(secrets or ())
        known.update(os.environ.get(ENV[name], '') for name in SECRET_FIELDS)
        try:
            local = _local()
            known.update(local.get(name, '') for name in SECRET_FIELDS)
        except Exception:
            pass
        known = sorted((v for v in known if isinstance(v, str) and v), key=len, reverse=True)

        def clean(value):
            if isinstance(value, dict):
                return {clean(str(k)): clean(v) for k, v in value.items()
                        if str(k).lower().replace('-', '_') not in _SENSITIVE
                        and not str(k).lower().replace('-', '_').endswith(('_api_key', '_secret', '_token', '_password'))}
            if isinstance(value, (list, tuple)):
                return [clean(v) for v in value]
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, (dict, list)):
                        value = json.dumps(clean(parsed), ensure_ascii=False)
                except (ValueError, TypeError):
                    pass
                value = re.sub(r'(https?://[^\s?"<>]+)\?[^\s"<>]+', r'\1?[redacted]', value, flags=re.I)
                for secret in known:
                    value = value.replace(secret, '[redacted]')
                return value
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return clean(str(value))

        identifier = uuid.uuid4().hex
        directory = data_directory() / 'diagnostics'
        directory.mkdir(parents=True, exist_ok=True)
        def package_version(name):
            try:
                return version(name)
            except PackageNotFoundError:
                return 'unknown'

        artifact = clean({'format_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                          'environment': {'python': platform.python_version(), 'pydantic': package_version('pydantic'), 'langgraph': package_version('langgraph')},
                          'redacted': True, 'kind': kind, 'payload': payload, 'schema': schema, 'response': response,
                          'errors': errors, 'context': {**_scope.get(), **(context or {})}})
        with (directory / (identifier + '.json')).open('x', encoding='utf-8') as output:
            json.dump(artifact, output, ensure_ascii=False, indent=2)
        return identifier
    except Exception as exc:
        logger.warning('diagnostic_write_failed error_type=%s', type(exc).__name__)
        return ''
