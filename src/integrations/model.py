"""OpenAI-compatible structured chat client."""
import json
import logging
import re
import time
from collections import OrderedDict
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

# These nodes return bounded decisions or tool choices. Writing stages are left
# uncapped so a valid report or evidence-backed draft is not cut off.
DECISION_OUTPUT_CAP = 4096
DECISION_PROMPTS = frozenset({
    'literature/background_investigator',
    'literature/planner',
    'literature/analyst',
})
UNSUPPORTED_REQUEST_PARAMS = OrderedDict()
MAX_UNSUPPORTED_REQUEST_PARAMS = 256
COMPATIBLE_OPTIONAL_PARAMS = ('stream', 'max_completion_tokens', 'reasoning_effort')

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
    context['OUTPUT_SCHEMA'] = json.dumps(json_schema, ensure_ascii=False, separators=(',', ':'))
    return PROMPT_ENV.get_template(prompt_id + '.md').render(**context)


def _approximate_text_units(text):
    """Estimate token-like input units without a provider tokenizer.

    Non-ASCII code points and punctuation count as one unit; ASCII letter and
    digit runs count at roughly four characters per unit. This is intentionally
    an estimate suitable for comparing prompt sizes, not a billing token count.
    """
    units = 0
    ascii_run = 0

    def flush_ascii_run():
        nonlocal ascii_run
        if ascii_run:
            units_add = max(1, (ascii_run + 3) // 4)
            ascii_run = 0
            return units_add
        return 0

    for char in text:
        if char.isascii() and char.isalnum():
            ascii_run += 1
        else:
            units += flush_ascii_run()
            if not char.isspace():
                units += 1
    units += flush_ascii_run()
    return units


def structured_input_units(prompt_id, payload, schema, *, template_context=None):
    """Estimate input size from the fully rendered system and user messages.

    A fixed timestamp keeps the result deterministic; the runtime timestamp has
    the same format and length. JSON serialization matches ``structured``.
    """
    json_schema = schema.model_json_schema()
    prompt = _render_prompt(
        prompt_id,
        template_context,
        json_schema,
        '2000-01-01T00:00:00+08:00',
    )
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    # Account for the two message boundaries/role labels in the chat envelope.
    return _approximate_text_units(prompt) + _approximate_text_units(payload_json) + 4


def _decision_request_options(prompt_id, payload, model_options):
    explicit_effort = model_options.get('reasoning_effort') if model_options else None
    if explicit_effort:
        effort = explicit_effort
    elif prompt_id in DECISION_PROMPTS or (
        prompt_id == 'literature/researcher'
        and isinstance(payload, dict)
        and payload.get('phase') == 'act'
    ):
        effort = 'low'
    else:
        effort = None

    options = {'reasoning_effort': effort} if effort else {}
    bounded_decision = prompt_id in DECISION_PROMPTS or (
        prompt_id == 'literature/researcher'
        and isinstance(payload, dict)
        and payload.get('phase') == 'act'
    )
    # Higher explicit effort can be needed for difficult planning or review;
    # preserve its room by disabling the output cap. Explicit low remains capped.
    if bounded_decision and (not explicit_effort or str(explicit_effort).lower() == 'low'):
        options['max_completion_tokens'] = DECISION_OUTPUT_CAP
    return options, bool(explicit_effort)


def _request_compatibility_key(config, parameter):
    """Return a bounded in-memory key without URL credentials or query data."""
    try:
        parsed = urlsplit(base_url(config.get('base_url', '')))
        endpoint = (
            parsed.scheme.lower(),
            (parsed.hostname or '').lower(),
            parsed.port,
            parsed.path.rstrip('/'),
        )
    except (AttributeError, TypeError, ValueError):
        endpoint = ('', '', None, '')
    return (*endpoint, config.get('model'), parameter)


def _parameter_is_known_unsupported(config, parameter):
    key = _request_compatibility_key(config, parameter)
    if key in UNSUPPORTED_REQUEST_PARAMS:
        UNSUPPORTED_REQUEST_PARAMS.move_to_end(key)
        return True
    return False


def _remember_unsupported_parameter(config, parameter):
    key = _request_compatibility_key(config, parameter)
    UNSUPPORTED_REQUEST_PARAMS[key] = None
    UNSUPPORTED_REQUEST_PARAMS.move_to_end(key)
    while len(UNSUPPORTED_REQUEST_PARAMS) > MAX_UNSUPPORTED_REQUEST_PARAMS:
        UNSUPPORTED_REQUEST_PARAMS.popitem(last=False)


def _reasoning_cap_compatibility_parameter(request_body):
    cap = request_body.get('max_completion_tokens')
    if request_body.get('reasoning_effort') == 'low' and type(cap) is int:
        return f'reasoning_effort=low+max_completion_tokens={cap}'
    return None


def _response_error_payloads(response):
    """Read JSON or SSE error envelopes without retaining response text."""
    response.read()
    text = response.text
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        body = None
    payloads = [body] if isinstance(body, dict) else []

    # Some compatible APIs return an OpenAI-style error envelope in an SSE
    # `data:` frame even for a rejected request.
    frame = []

    def append_frame():
        if not frame:
            return
        data = '\n'.join(frame).strip()
        frame.clear()
        if not data or data == '[DONE]':
            return
        try:
            value = json.loads(data)
        except (ValueError, TypeError):
            return
        if isinstance(value, dict):
            payloads.append(value)

    for line in text.splitlines():
        if not line:
            append_frame()
        elif line.startswith('data:'):
            frame.append(line[5:].lstrip())
    append_frame()
    return payloads


def _rejected_optional_parameter(response, request_body):
    """Identify only an explicit 400/422 rejection of a sent option."""
    if response.status_code not in (400, 422):
        return None
    try:
        payloads = _response_error_payloads(response)
    except (ValueError, TypeError):
        return None
    for body in payloads:
        error = body.get('error') if isinstance(body.get('error'), dict) else body
        parameter = error.get('param', body.get('param'))
        parameter = parameter.rsplit('.', 1)[-1].lower() if isinstance(parameter, str) else None
        # Retain the prior stream fallback contract, including providers that only
        # identify the rejected field in `param` and omit a useful message.
        if parameter == 'stream' and 'stream' in request_body:
            return 'stream'
        raw_message = error.get('message', body.get('message', ''))
        message = raw_message.lower() if isinstance(raw_message, str) else ''
        code = error.get('code', body.get('code', ''))
        code = code.lower() if isinstance(code, str) else ''

        # DashScope reports the low-effort conflict as a concrete cap versus
        # thinking-budget comparison. Retry only when those numbers match the
        # request and removing this cap can resolve the stated constraint.
        conflict = re.fullmatch(
            r'max_completion_tokens\s*\[(\d+)\]\s*must be greater than\s*thinking_budget\s*\[(\d+)\]',
            raw_message.strip(),
            flags=re.IGNORECASE,
        ) if isinstance(raw_message, str) else None
        compatibility_parameter = _reasoning_cap_compatibility_parameter(request_body)
        if conflict and compatibility_parameter:
            requested_cap, thinking_budget = (int(value) for value in conflict.groups())
            if requested_cap == request_body['max_completion_tokens'] and requested_cap <= thinking_budget:
                return compatibility_parameter

        if (
            request_body.get('reasoning_effort') == 'low'
            and 'reasoning_effort' in message
            and 'low' in message
            and any(term in message for term in ('unsupported value', 'invalid value', 'not a valid value', 'unsupported level', 'not supported'))
        ):
            # This only rejects the default effort value. The parameter may still
            # support another user-selected level, so cache the value separately.
            return 'reasoning_effort=low'
        if any(term in message for term in ('unsupported value', 'invalid value', 'not a valid value', 'allowed values')):
            continue
        explicit_parameter_rejection = (
            any(term in code for term in ('unsupported_parameter', 'unknown_parameter', 'unrecognized_parameter', 'unexpected_parameter'))
            or any(term in message for term in (
                'unsupported parameter', 'unknown parameter', 'unrecognized parameter',
                'unexpected parameter', 'invalid parameter', 'parameter is not supported',
            ))
        )
        if explicit_parameter_rejection:
            for candidate in COMPATIBLE_OPTIONAL_PARAMS:
                if candidate in request_body and candidate in message:
                    return candidate
            if parameter in COMPATIBLE_OPTIONAL_PARAMS and parameter in request_body:
                return parameter
        if 'stream' in request_body and 'stream' in message and any(term in message for term in (
            'unsupported', 'not supported', 'not support', 'unknown parameter',
            'unrecognized parameter', 'unexpected parameter', 'invalid parameter',
        )):
            return 'stream'
    return None


def _json_without_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _repair_reporter_legacy_limitations(content, validation_error, schema):
    """Move only the known legacy sections[].limitations field, then validate."""
    errors = validation_error.errors(include_input=False, include_context=False, include_url=False)
    if not errors or any(
        error.get('type') != 'extra_forbidden'
        or len(error.get('loc', ())) != 3
        or error['loc'][0] != 'sections'
        or type(error['loc'][1]) is not int
        or error['loc'][2] != 'limitations'
        for error in errors
    ):
        return None
    try:
        report = json.loads(content, object_pairs_hook=_json_without_duplicate_keys)
    except (ValueError, TypeError):
        return None
    if not isinstance(report, dict) or not isinstance(report.get('sections'), list):
        return None
    if not all(isinstance(section, dict) for section in report['sections']):
        return None
    legacy_limitations = []
    for section in report['sections']:
        if 'limitations' not in section:
            continue
        value = section['limitations']
        if not isinstance(value, list):
            return None
        legacy_limitations.extend(value)
        del section['limitations']
    if not legacy_limitations:
        return None
    top_level = report.get('limitations', [])
    if not isinstance(top_level, list):
        return None
    report['limitations'] = top_level + legacy_limitations
    repaired = json.dumps(report, ensure_ascii=False)
    try:
        schema.model_validate_json(repaired)
    except ValidationError:
        return None
    return repaired


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
        choice = body['choices'][0]
        content = choice['message']['content']
        finish_reason = choice.get('finish_reason')
        if isinstance(content, str) and content:
            display = content
            for secret in secrets:
                display = display.replace(secret, '[REDACTED]')
            _emit_stream(on_stream, 'model.delta', {'attempt': attempt, 'phase': phase, 'text': display})
        return content, usage, finish_reason

    parts = []
    pending = []
    pending_size = 0
    display_buffer = ''
    usage = None
    finish_reason = None

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
            event_finish_reason = choices[0].get('finish_reason')
            if isinstance(event_finish_reason, str):
                finish_reason = event_finish_reason
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
    return ''.join(parts), usage, finish_reason


def _disable_rejected_optional_parameter(response, request_body, config, explicit_effort):
    rejected = _rejected_optional_parameter(response, request_body)
    if not rejected:
        return False
    if rejected == _reasoning_cap_compatibility_parameter(request_body):
        request_body.pop('max_completion_tokens', None)
        _remember_unsupported_parameter(config, rejected)
        logger.info('model_option_fallback option=max_completion_tokens reason=reasoning_budget_conflict status=%d',
                    response.status_code)
        return True
    if rejected in ('reasoning_effort', 'reasoning_effort=low') and explicit_effort:
        response.raise_for_status()
    if rejected == 'reasoning_effort=low':
        request_body.pop('reasoning_effort', None)
    else:
        request_body.pop(rejected, None)
    _remember_unsupported_parameter(config, rejected)
    return True


def _post_structured(config, headers, request_body, *, on_stream, attempt, phase, secrets, explicit_effort):
    """Send one completion, retrying only explicit pre-response option rejections."""
    url = base_url(config['base_url']) + '/chat/completions'
    for _ in range(len(COMPATIBLE_OPTIONAL_PARAMS) + 1):
        if request_body.get('stream') is True:
            with httpx.stream('POST', url, headers=headers, json=request_body, timeout=90) as response:
                if _disable_rejected_optional_parameter(response, request_body, config, explicit_effort):
                    continue
                response.raise_for_status()
                content, usage, finish_reason = _stream_response(response, on_stream, attempt, phase, secrets)
            return response, content, usage, finish_reason, True

        response = httpx.post(url, headers=headers, json=request_body, timeout=90)
        if _disable_rejected_optional_parameter(response, request_body, config, explicit_effort):
            continue
        response.raise_for_status()
        body = response.json()
        usage = body.get('usage') if isinstance(body, dict) else None
        choice = body['choices'][0]
        return response, choice['message']['content'], usage, choice.get('finish_reason'), False

    # Reaching here means a provider kept rejecting options after all of them
    # were removed. Surface its last HTTP error rather than retrying generation.
    response.raise_for_status()
    raise RuntimeError('模型服务无法处理请求参数')


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
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    options, explicit_effort = _decision_request_options(prompt_id, payload, model_options or {})
    for parameter in tuple(options):
        if parameter == 'reasoning_effort' and explicit_effort:
            continue
        if parameter == 'reasoning_effort' and _parameter_is_known_unsupported(config, 'reasoning_effort=low'):
            options.pop(parameter, None)
            continue
        if parameter == 'max_completion_tokens':
            compatibility_parameter = _reasoning_cap_compatibility_parameter(options)
            if compatibility_parameter and _parameter_is_known_unsupported(config, compatibility_parameter):
                options.pop(parameter, None)
                continue
        if _parameter_is_known_unsupported(config, parameter):
            options.pop(parameter, None)
    validation_feedback = None
    for attempt in (1, 2):
        attempt_started = time.monotonic()
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
        prompt = _render_prompt(prompt_id, template_context, json_schema, now)
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': payload_json}]
        if validation_feedback is not None:
            messages.append({'role': 'user', 'content': json.dumps({'validation_feedback': validation_feedback}, ensure_ascii=False, separators=(',', ':'))})
        system_chars = len(messages[0]['content'])
        user_chars = sum(len(message['content']) for message in messages[1:])
        logger.info('model_call started run_id=%s prompt_id=%s phase=%s attempt=%d system_chars=%d user_chars=%d input_chars=%d',
                    run_id if run_id is not None else 'na', prompt_id, phase, attempt, system_chars, user_chars,
                    system_chars + user_chars)
        content = None
        usage = None
        finish_reason = None
        context = {'prompt_id': prompt_id, 'attempt': attempt, 'rendered_prompt': prompt, 'model': config['model'], 'model_options': options, 'messages': messages}
        secrets = (config['api_key'],) if config.get('api_key') else ()
        try:
            request_body = {'model': config['model'], 'messages': list(messages),
                            'response_format': {'type': 'json_object'}, **options}
            stream_request = on_stream is not None and not _parameter_is_known_unsupported(config, 'stream')
            if on_stream is not None:
                _emit_stream(on_stream, 'model.started', {'attempt': attempt, 'phase': phase})
            if stream_request:
                request_body['stream'] = True
            response, content, usage, finish_reason, streamed = _post_structured(
                config,
                headers,
                request_body,
                on_stream=on_stream,
                attempt=attempt,
                phase=phase,
                secrets=secrets,
                explicit_effort=explicit_effort,
            )
            # _post_structured may have removed an explicitly rejected option.
            # Keep that decision for this call's schema-correction attempt too.
            for parameter in tuple(options):
                if parameter not in request_body:
                    options.pop(parameter, None)
            if on_stream is not None and not streamed and isinstance(content, str) and content:
                display = content
                for secret in secrets:
                    display = display.replace(secret, '[REDACTED]')
                _emit_stream(on_stream, 'model.delta', {'attempt': attempt, 'phase': phase, 'text': display})
            try:
                result = schema.model_validate_json(content)
            except ValidationError as exc:
                repaired = (
                    _repair_reporter_legacy_limitations(content, exc, schema)
                    if prompt_id == 'literature/reporter'
                    else None
                )
                if repaired is None:
                    raise
                result = schema.model_validate_json(repaired)
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
            output_cap_reached = finish_reason == 'length' and 'max_completion_tokens' in request_body
            if attempt == 2 or output_cap_reached:
                raise StructuredOutputError('模型返回的结构化结果无效') from None
            validation_feedback = errors
