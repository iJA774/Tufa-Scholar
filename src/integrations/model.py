"""OpenAI-compatible structured chat client."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError
from src.infrastructure import settings
from src.infrastructure.diagnostics import diagnostic_context, record_failure

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parents[1] / 'prompts'
PROMPT_IDS = frozenset({
    'runtime/dispatcher',
    'literature/background_investigator',
    'literature/planner',
    'literature/researcher',
    'literature/analyst',
    'literature/reporter',
})
PROMPT_ENV = Environment(
    loader=FileSystemLoader(str(PROMPT_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
)
DEFAULT_TEMPLATE_CONTEXT = {
    'node_role': '',
    'search_mode': 'none',
    'research_mode': 'auto',
    'available_tools': [],
    'local_papers': [],
}

# Only root addresses of known OpenAI-compatible endpoints are completed.
ROOT_PATHS = {
    'api.openai.com': '/v1',
    'generativelanguage.googleapis.com': '/v1beta/openai',
    'dashscope.aliyuncs.com': '/compatible-mode/v1',
    'dashscope-intl.aliyuncs.com': '/compatible-mode/v1',
    'cn-hongkong.dashscope.aliyuncs.com': '/compatible-mode/v1',
    'ark.cn-beijing.volces.com': '/api/v3',
    'tokenhub.tencentmaas.com': '/v1',
    'api.siliconflow.cn': '/v1',
    'api.moonshot.cn': '/v1',
}


def base_url(url):
    url = url.rstrip('/')
    parsed = urlsplit(url)
    if not parsed.path:
        suffix = ROOT_PATHS.get(parsed.hostname.lower(), '')
        if parsed.hostname.lower().endswith('.maas.aliyuncs.com'):
            suffix = '/compatible-mode/v1'
        return url + suffix
    return url


def bailian_url(url):
    parsed = urlsplit(base_url(url))
    host = parsed.hostname.lower()
    official = host in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'cn-hongkong.dashscope.aliyuncs.com') or host.endswith('.maas.aliyuncs.com')
    if official and parsed.path == '/compatible-mode/v1':
        return f'{parsed.scheme}://{parsed.netloc}/api/v1/models'
    return None


def discover(url, key, *, details=False):
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    try:
        catalog = bailian_url(url)
        response = httpx.get(base_url(url) + '/models', headers=headers, timeout=15, follow_redirects=False)
        if catalog and response.status_code in (404, 405):
            rows = []
            page = 1
            received = 0
            while True:
                response = httpx.get(catalog, headers=headers, params={'page_no': page, 'page_size': 100}, timeout=15, follow_redirects=False)
                response.raise_for_status()
                output = response.json()['output']
                data = output['models']
                total = output['total']
                if not isinstance(data, list) or not isinstance(total, int) or total < 0:
                    raise ValueError()
                rows.extend(dict(item, id=item['model']) for item in data if isinstance(item, dict) and isinstance(item.get('model'), str) and item['model'])
                received += len(data)
                if received >= total:
                    break
                if not data:
                    raise ValueError()
                page += 1
        else:
            response.raise_for_status()
            data = response.json()['data']
            if not isinstance(data, list):
                raise ValueError()
            rows = [item for item in data if isinstance(item, dict) and isinstance(item.get('id'), str) and item['id']]
        ids = [item['id'] for item in rows]
        if key and any(key in model for model in ids):
            raise ValueError()
        if not details:
            return list(dict.fromkeys(ids))
        models = {}
        for item in rows:
            effort = item.get('effort')
            levels = effort.get('supported_levels', []) if isinstance(effort, dict) else []
            levels = list(dict.fromkeys(v for v in levels if isinstance(v, str) and v)) if isinstance(levels, list) else []
            default = effort.get('default_level') if isinstance(effort, dict) else None
            models[item['id']] = {'id': item['id'], 'reasoning_efforts': levels,
                                  'default_effort': default if default in levels else None}
        result = list(models.values())
        if key and key in json.dumps(result, ensure_ascii=False):
            raise ValueError()
        return result
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f'获取模型失败 (HTTP {exc.response.status_code})，请检查地址和密钥，或手动填写模型名称') from None
    except httpx.RequestError:
        raise RuntimeError('无法连接模型服务，请检查地址，或手动填写模型名称') from None
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('模型服务返回的数据无效，请手动填写模型名称') from None


class ModelServiceError(RuntimeError):
    """Model service HTTP or transport failure."""


class StructuredOutputError(RuntimeError):
    """Model response remained invalid after format correction."""


def _safe_validation_errors(exc, json_schema):
    # Only field names declared by the schema can appear in feedback or logs.
    fields = set()
    def collect(value):
        if isinstance(value, dict):
            fields.update(value.get('properties', {}).keys())
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(json_schema)
    allowed_types = {'json_invalid', 'missing', 'literal_error', 'string_type', 'list_type',
                     'bool_type', 'model_type', 'extra_forbidden', 'too_long', 'too_short',
                     'value_error'}
    result = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        path = [part if type(part) is int or (isinstance(part, str) and part in fields) else '*' for part in error['loc']]
        result.append({'type': error['type'] if error['type'] in allowed_types else 'validation_error',
                       'path': path})
    return result


def _render_prompt(prompt_id, template_context, json_schema, current_time):
    if prompt_id not in PROMPT_IDS:
        raise ValueError(f'未知模型提示词 ID：{prompt_id}')
    context = {**DEFAULT_TEMPLATE_CONTEXT, **(template_context or {})}
    # These values are generated by the integration boundary and cannot be
    # replaced by workflow context.
    context['CURRENT_TIME'] = current_time
    context['OUTPUT_SCHEMA'] = json.dumps(json_schema, ensure_ascii=False)
    return PROMPT_ENV.get_template(prompt_id + '.md').render(**context)


def _usage_log_fields(usage):
    """Return only standard numeric usage fields, never provider response text."""
    usage = usage if isinstance(usage, dict) else {}

    def count(value):
        return value if type(value) is int and value >= 0 else None

    prompt_details = usage.get('prompt_tokens_details')
    completion_details = usage.get('completion_tokens_details')
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    values = {
        'prompt_tokens': count(usage.get('prompt_tokens')),
        'completion_tokens': count(usage.get('completion_tokens')),
        'total_tokens': count(usage.get('total_tokens')),
        'cached_tokens': count(prompt_details.get('cached_tokens')),
        'reasoning_tokens': count(completion_details.get('reasoning_tokens')),
    }
    return ' '.join(f'{key}={value if value is not None else "na"}' for key, value in values.items())


def _emit_stream(callback, kind, data):
    """Send public model lifecycle events without letting observers break the call."""
    if callback is None:
        return
    try:
        callback(kind, data)
    except Exception:
        logger.warning('model_stream callback failed kind=%s', kind)


def _explicitly_rejects_stream(response):
    """Recognize only clear request-validation errors for the stream option."""
    if response.status_code not in (400, 422):
        return False
    try:
        # Responses returned by httpx.stream are not buffered until read().
        response.read()
        body = response.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(body, dict):
        return False
    error = body.get('error') if isinstance(body.get('error'), dict) else body
    param = error.get('param', body.get('param'))
    if isinstance(param, str) and param.lower() == 'stream':
        return True
    message = error.get('message', body.get('message', ''))
    if not isinstance(message, str):
        return False
    message = message.lower()
    return 'stream' in message and any(term in message for term in (
        'unsupported', 'not supported', 'not support', 'unknown parameter',
        'unrecognized parameter', 'unexpected parameter', 'invalid parameter',
    ))


def _stream_response(response, on_stream, attempt, phase, secrets=()):
    """Read an OpenAI-compatible SSE stream, or fall back to a regular JSON body."""
    content_type = response.headers.get('content-type', '').lower()
    if 'text/event-stream' not in content_type:
        # A few compatible endpoints and fixture servers ignore stream=true and
        # return the normal completion envelope. Keep that path usable.
        # `httpx.stream` deliberately leaves response bodies unread.
        response.read()
        body = response.json()
        usage = body.get('usage') if isinstance(body, dict) else None
        content = body['choices'][0]['message']['content']
        if isinstance(content, str) and content:
            display = content
            for secret in secrets:
                display = display.replace(secret, '[REDACTED]')
            _emit_stream(on_stream, 'model.delta', {'attempt': attempt, 'phase': phase, 'text': display})
        return content, usage

    parts = []
    pending = []
    pending_size = 0
    display_buffer = ''
    usage = None

    def flush(final=False):
        nonlocal pending_size, display_buffer
        if pending:
            text = ''.join(pending)
            parts.append(text)
            display_buffer += text
            pending.clear()
            pending_size = 0
        if not display_buffer:
            return
        max_secret_length = max((len(secret) for secret in secrets if secret), default=0)
        cut = len(display_buffer) if final or not max_secret_length else max(0, len(display_buffer) - max_secret_length + 1)
        if not final:
            for secret in secrets:
                if not secret:
                    continue
                start = display_buffer.find(secret)
                while start >= 0:
                    end = start + len(secret)
                    if start < cut < end:
                        cut = start
                    start = display_buffer.find(secret, start + 1)
        text, display_buffer = display_buffer[:cut], display_buffer[cut:]
        if text:
            for secret in secrets:
                if secret:
                    text = text.replace(secret, '[REDACTED]')
            _emit_stream(on_stream, 'model.delta', {'attempt': attempt, 'phase': phase, 'text': text})

    try:
        for line in response.iter_lines():
            if not line or not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            try:
                event = json.loads(data)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            event_usage = event.get('usage')
            if isinstance(event_usage, dict):
                usage = event_usage
            choices = event.get('choices')
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get('delta')
            if not isinstance(delta, dict):
                continue
            # Deliberately ignore reasoning_content, tool calls and every field
            # except user-facing textual content.
            text = delta.get('content')
            if isinstance(text, str) and text:
                pending.append(text)
                pending_size += len(text)
                # Avoid a UI event for every tiny token while keeping output live.
                if pending_size >= 24:
                    flush()
    except httpx.RequestError:
        # Preserve already received content in the UI even if the stream later
        # fails; the caller will report failure and must not retry this request.
        flush(final=True)
        raise
    flush(final=True)
    return ''.join(parts), usage


def structured(prompt_id, payload, schema, *, model_options=None, template_context=None, on_stream=None):
    config = settings.model_config()
    if model_options and model_options.get('model_name'):
        config = dict(config, model=model_options['model_name'])
    if not config.get('base_url') or not config.get('model'):
        raise RuntimeError('请先在设置中配置模型地址和模型名称')
    json_schema = schema.model_json_schema()
    headers = {'Authorization': 'Bearer ' + config['api_key']} if config.get('api_key') else {}
    scope = diagnostic_context()
    run_id = scope.get('run_id')
    phase = payload.get('phase') if isinstance(payload, dict) else None
    if phase not in {'query', 'select', 'act', 'draft'}:
        phase = 'na'
    payload_json = json.dumps(payload, ensure_ascii=False)
    options = {'reasoning_effort': model_options['reasoning_effort']} if model_options and model_options.get('reasoning_effort') else {}
    validation_feedback = None
    for attempt in (1, 2):
        attempt_started = time.monotonic()
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
        prompt = _render_prompt(prompt_id, template_context, json_schema, now)
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': payload_json}]
        if validation_feedback is not None:
            messages.append({'role': 'user', 'content': json.dumps({'validation_feedback': validation_feedback}, ensure_ascii=False)})
        system_chars = len(messages[0]['content'])
        user_chars = sum(len(message['content']) for message in messages[1:])
        logger.info('model_call started run_id=%s prompt_id=%s phase=%s attempt=%d system_chars=%d user_chars=%d input_chars=%d',
                    run_id if run_id is not None else 'na', prompt_id, phase, attempt, system_chars, user_chars,
                    system_chars + user_chars)
        content = None
        usage = None
        context = {'prompt_id': prompt_id, 'attempt': attempt, 'rendered_prompt': prompt, 'model': config['model'], 'model_options': options, 'messages': messages}
        secrets = (config['api_key'],) if config.get('api_key') else ()
        try:
            request_body = {'model': config['model'], 'messages': list(messages),
                            'response_format': {'type': 'json_object'}, **options}
            if on_stream is None:
                response = httpx.post(base_url(config['base_url']) + '/chat/completions', headers=headers,
                                      json=request_body, timeout=90)
                response.raise_for_status()
                content = response.text
                body = response.json()
                usage = body.get('usage') if isinstance(body, dict) else None
                content = body['choices'][0]['message']['content']
            else:
                request_body['stream'] = True
                _emit_stream(on_stream, 'model.started', {'attempt': attempt, 'phase': phase})
                fallback_to_nonstream = False
                with httpx.stream('POST', base_url(config['base_url']) + '/chat/completions', headers=headers,
                                  json=request_body, timeout=90) as response:
                    if _explicitly_rejects_stream(response):
                        fallback_to_nonstream = True
                    else:
                        response.raise_for_status()
                        content, usage = _stream_response(response, on_stream, attempt, phase, secrets)
                if fallback_to_nonstream:
                    # Some otherwise compatible providers reject the optional
                    # stream flag. Retry once without it only on an explicit
                    # 400/422 validation error naming that parameter.
                    request_body.pop('stream')
                    response = httpx.post(base_url(config['base_url']) + '/chat/completions', headers=headers,
                                          json=request_body, timeout=90)
                    response.raise_for_status()
                    body = response.json()
                    usage = body.get('usage') if isinstance(body, dict) else None
                    content = body['choices'][0]['message']['content']
                    if isinstance(content, str) and content:
                        display = content
                        for secret in secrets:
                            display = display.replace(secret, '[REDACTED]')
                        _emit_stream(on_stream, 'model.delta', {'attempt': attempt, 'phase': phase, 'text': display})
            result = schema.model_validate_json(content)
            logger.info('model_call completed run_id=%s prompt_id=%s phase=%s attempt=%d elapsed_ms=%d status=%d %s',
                        run_id if run_id is not None else 'na', prompt_id, phase, attempt,
                        int((time.monotonic() - attempt_started) * 1000), response.status_code,
                        _usage_log_fields(usage))
            _emit_stream(on_stream, 'model.completed', {'attempt': attempt, 'phase': phase})
            return result
        except httpx.HTTPStatusError as exc:
            _emit_stream(on_stream, 'model.failed', {'attempt': attempt, 'phase': phase})
            diagnostic_id = record_failure(kind='http_error', payload=payload, schema=json_schema, errors={'type': type(exc).__name__, 'status': exc.response.status_code}, context=context, secrets=secrets)
            logger.warning('model_call failed run_id=%s prompt_id=%s phase=%s attempt=%d elapsed_ms=%d status=%d error_type=%s diagnostic_id=%s',
                           run_id if run_id is not None else 'na', prompt_id, phase, attempt,
                           int((time.monotonic() - attempt_started) * 1000), exc.response.status_code,
                           type(exc).__name__, diagnostic_id)
            raise ModelServiceError(f'模型服务请求失败 (HTTP {exc.response.status_code})') from None
        except httpx.RequestError as exc:
            # A partial stream may already have been billed; never retry it.
            _emit_stream(on_stream, 'model.failed', {'attempt': attempt, 'phase': phase})
            diagnostic_id = record_failure(kind='network_error', payload=payload, schema=json_schema, errors={'type': type(exc).__name__}, context=context, secrets=secrets)
            logger.warning('model_call failed run_id=%s prompt_id=%s phase=%s attempt=%d elapsed_ms=%d error_type=%s diagnostic_id=%s',
                           run_id if run_id is not None else 'na', prompt_id, phase, attempt,
                           int((time.monotonic() - attempt_started) * 1000), type(exc).__name__, diagnostic_id)
            raise ModelServiceError('模型服务连接失败或超时，请检查设置') from None
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            errors = _safe_validation_errors(exc, json_schema) if isinstance(exc, ValidationError) else [{'type': 'invalid_response', 'path': []}]
            _emit_stream(on_stream, 'model.failed', {'attempt': attempt, 'phase': phase})
            diagnostic_id = record_failure(kind='model_validation', payload=payload, schema=json_schema, response=content, errors=errors, context=context, secrets=secrets)
            logger.warning('model_call invalid run_id=%s prompt_id=%s phase=%s attempt=%d errors=%s diagnostic_id=%s %s',
                           run_id if run_id is not None else 'na', prompt_id, phase, attempt,
                           json.dumps(errors, ensure_ascii=False), diagnostic_id,
                           _usage_log_fields(usage))
            if attempt == 2:
                raise StructuredOutputError('模型返回的结构化结果无效') from None
            validation_feedback = errors
