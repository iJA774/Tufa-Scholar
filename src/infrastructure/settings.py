"""Local configuration; environment values take precedence over SQLite."""
import os
import re
from urllib.parse import urlsplit
from src.persistence.storage import connect_database, data_directory

ENV = {
    'openalex_api_key': 'OPENALEX_API_KEY',
    'openai_api_key': 'OPENAI_API_KEY',
    'openai_base_url': 'OPENAI_BASE_URL',
    'openai_model': 'OPENAI_MODEL',
    'web_search_engine': 'SEARCH_API',
    'tavily_api_key': 'TAVILY_API_KEY',
    'infoquest_api_key': 'INFOQUEST_API_KEY',
    'brave_search_api_key': 'BRAVE_SEARCH_API_KEY',
    'serper_api_key': 'SERPER_API_KEY',
    'searx_base_url': 'SEARX_HOST',
    'wikipedia_lang': 'WIKIPEDIA_LANG',
}

SEARCH_PROVIDERS = (
    ('tavily', 'Tavily', True),
    ('infoquest', 'InfoQuest', True),
    ('duckduckgo', 'DuckDuckGo', False),
    ('brave_search', 'Brave Search', True),
    ('serper', 'Serper', True),
    ('arxiv', 'arXiv', False),
    ('searx', 'SearX', False),
    ('wikipedia', 'Wikipedia', False),
)
SECRET_FIELDS = ('openalex_api_key', 'openai_api_key', 'tavily_api_key',
                 'infoquest_api_key', 'brave_search_api_key', 'serper_api_key')


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
    engine, engine_source = _resolve_default('web_search_engine', local, 'duckduckgo')
    searx_url, searx_source = _resolve('searx_base_url', local)
    language, language_source = _resolve_default('wikipedia_lang', local, 'zh')
    providers = []
    for provider_id, name, requires_key in SEARCH_PROVIDERS:
        provider_key, source = _resolve(provider_id + '_api_key', local) if requires_key else (None, 'none')
        providers.append({'id': provider_id, 'name': name, 'requires_key': requires_key,
                          'key_configured': bool(provider_key), 'key_source': source})
    return {'openalex': {'key_configured': bool(alex), 'key_source': alex_source},
            'model': {'base_url': url, 'base_url_source': url_source,
                      'model': model, 'model_source': model_source,
                      'key_configured': bool(key), 'key_source': key_source},
            'web_search': {'engine': engine, 'engine_source': engine_source,
                           'providers': providers, 'searx_base_url': searx_url,
                           'searx_base_url_source': searx_source,
                           'wikipedia_lang': language, 'wikipedia_lang_source': language_source}}


def _resolve_default(name, local, default):
    value, source = _resolve(name, local)
    return (value, source) if value else (default, 'default')


def validate_base_url(url, label='模型地址'):
    if url is not None and url.strip():
        try:
            parsed = urlsplit(url.strip())
            valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                     and parsed.port != 0 and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment and not any(ch.isspace() for ch in url))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f'{label}必须是有效的 HTTP 或 HTTPS URL，且不含凭据、查询参数或片段')


def validate_web_search(engine, searx_url, language):
    if engine and engine.strip() and engine.strip() not in {provider[0] for provider in SEARCH_PROVIDERS}:
        raise ValueError('不支持的网页搜索引擎')
    validate_base_url(searx_url, 'SearX 地址')
    if language and language.strip() and not re.fullmatch(r'[a-z]{2,12}(?:-[a-z0-9]{1,12})*', language.strip()):
        raise ValueError('维基百科语言必须是有效的小写语言代码')


def origin(url):
    parsed = urlsplit(url)
    return (parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme.lower() == 'https' else 80))


def update(values):
    url = values.get('openai_base_url')
    validate_base_url(url)
    validate_web_search(values.get('web_search_engine'), values.get('searx_base_url'), values.get('wikipedia_lang'))
    current_url = model_config()['base_url']
    changed_origin = bool(not os.environ.get('OPENAI_BASE_URL') and url and url.strip() and current_url and origin(url.strip()) != origin(current_url))
    if changed_origin and os.environ.get('OPENAI_API_KEY') and not os.environ.get('OPENAI_BASE_URL'):
        raise ValueError('更换服务地址前，请先更新环境变量中的模型密钥')
    with connect_database() as db:
        if changed_origin and not (values.get('openai_api_key') or '').strip():
            db.execute("DELETE FROM settings WHERE key='openai_api_key'")
        for name in ENV:
            value = values.get(name)
            if name in SECRET_FIELDS:
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
    from src.infrastructure.logging_setup import refresh_secrets
    refresh_secrets()
    return public_settings()


def model_config():
    local = _local()
    return {'base_url': _resolve('openai_base_url', local)[0], 'api_key': _resolve('openai_api_key', local)[0], 'model': _resolve('openai_model', local)[0]}


def web_search_config():
    # Resolve on each call so the worker picks up changes without restarting.
    local = _local()
    engine = _resolve_default('web_search_engine', local, 'duckduckgo')[0].strip() or 'duckduckgo'
    base_url = _resolve('searx_base_url', local)[0] if engine == 'searx' else None
    base_url = base_url.strip() if base_url else None
    language = _resolve_default('wikipedia_lang', local, 'zh')[0].strip() or 'zh'
    validate_web_search(engine, base_url, language)
    requires_key = next(provider[2] for provider in SEARCH_PROVIDERS if provider[0] == engine)
    key = _resolve(engine + '_api_key', local)[0] if requires_key else None
    return {'engine': engine, 'api_key': key, 'base_url': base_url, 'wikipedia_lang': language}
