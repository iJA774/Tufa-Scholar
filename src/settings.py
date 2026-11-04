"""Local configuration; environment values take precedence over SQLite."""
import os
from urllib.parse import urlsplit
from src.storage import connect_database, data_directory

ENV = {
    'openalex_api_key': 'OPENALEX_API_KEY',
    'openai_api_key': 'OPENAI_API_KEY',
    'openai_base_url': 'OPENAI_BASE_URL',
    'openai_model': 'OPENAI_MODEL',
}


def _local():
    if not (data_directory() / 'research.db').exists():
        return {}
    with connect_database() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return {}
        return dict(db.execute('SELECT key, value FROM settings').fetchall())


def _resolve(name, local):
    value = os.environ.get(ENV[name])
    if value:
        return value, 'environment'
    value = local.get(name)
    return (value, 'local') if value else (None, 'none')


def openalex_key():
    # Called for every request so a running worker picks up settings changes.
    if os.environ.get('OPENALEX_API_KEY'):
        return os.environ['OPENALEX_API_KEY']
    return _resolve('openalex_api_key', _local())[0]


def public_settings():
    local = _local()
    alex, alex_source = _resolve('openalex_api_key', local)
    key, key_source = _resolve('openai_api_key', local)
    url, url_source = _resolve('openai_base_url', local)
    model, model_source = _resolve('openai_model', local)
    return {'openalex': {'key_configured': bool(alex), 'key_source': alex_source},
            'model': {'base_url': url, 'base_url_source': url_source,
                      'model': model, 'model_source': model_source,
                      'key_configured': bool(key), 'key_source': key_source}}


def update(values):
    url = values.get('openai_base_url')
    if url is not None and url.strip():
        try:
            parsed = urlsplit(url.strip())
            valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                     and parsed.port != 0 and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment and not any(ch.isspace() for ch in url))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('模型地址必须是有效的 HTTP 或 HTTPS URL，且不含凭据、查询参数或片段')
    with connect_database() as db:
        for name in ENV:
            value = values.get(name)
            if name in ('openalex_api_key', 'openai_api_key'):
                if values.get('clear_' + name):
                    db.execute('DELETE FROM settings WHERE key=?', (name,))
                    continue
                if not value or not value.strip():
                    continue
            elif value is None:
                continue
            value = value.strip()
            if not value:
                db.execute('DELETE FROM settings WHERE key=?', (name,))
            else:
                db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (name, value))
        db.commit()
    return public_settings()
