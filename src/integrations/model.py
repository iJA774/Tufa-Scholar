"""OpenAI-compatible structured chat client."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from pydantic import ValidationError
from src.infrastructure import settings
from src.infrastructure.diagnostics import record_failure

logger = logging.getLogger(__name__)

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
                     'string_too_long', 'string_too_short', 'value_error',
                     'specialist_search_query_required', 'specialist_read_candidates_required',
                     'specialist_web_target_required', 'specialist_direct_query_required',
                     'specialist_plan_invalid'}
    result = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        path = [part if type(part) is int or (isinstance(part, str) and part in fields) else '*' for part in error['loc']]
        result.append({'type': error['type'] if error['type'] in allowed_types else 'validation_error',
                       'path': path})
    return result


def structured(prompt_id, payload, schema, *, model_options=None):
    config = settings.model_config()
    if model_options and model_options.get('model_name'):
        config = dict(config, model=model_options['model_name'])
    if not config.get('base_url') or not config.get('model'):
        raise RuntimeError('请先在设置中配置模型地址和模型名称')
    prompt_dir = Path(__file__).resolve().parents[1] / 'prompts'
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    prompt = (prompt_dir / (prompt_id + '.md')).read_text(encoding='utf-8').replace('{{ CURRENT_TIME }}', now)
    contract = (prompt_dir / 'runtime' / 'structured_output.md').read_text(encoding='utf-8')
    contract = contract.split('---', 2)[2].strip()
    json_schema = schema.model_json_schema()
    prompt += '\n\n' + contract.replace('{{ OUTPUT_SCHEMA }}', json.dumps(json_schema, ensure_ascii=False))
    headers = {'Authorization': 'Bearer ' + config['api_key']} if config.get('api_key') else {}
    start = time.monotonic()
    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    options = {'reasoning_effort': model_options['reasoning_effort']} if model_options and model_options.get('reasoning_effort') else {}
    for attempt in (1, 2):
        logger.info('model_call started prompt_id=%s attempt=%d', prompt_id, attempt)
        content = None
        context = {'prompt_id': prompt_id, 'attempt': attempt, 'rendered_prompt': prompt, 'model': config['model'], 'model_options': options, 'messages': messages}
        secrets = (config['api_key'],) if config.get('api_key') else ()
        try:
            response = httpx.post(base_url(config['base_url']) + '/chat/completions', headers=headers,
                                  json={'model': config['model'], 'messages': list(messages),
                                        'response_format': {'type': 'json_object'}, **options}, timeout=90)
            response.raise_for_status()
            content = response.text
            content = response.json()['choices'][0]['message']['content']
            result = schema.model_validate_json(content)
            logger.info('model_call completed prompt_id=%s attempt=%d elapsed_ms=%d status=%d', prompt_id, attempt, int((time.monotonic() - start) * 1000), response.status_code)
            return result
        except httpx.HTTPStatusError as exc:
            diagnostic_id = record_failure(kind='http_error', payload=payload, schema=json_schema, errors={'type': type(exc).__name__, 'status': exc.response.status_code}, context=context, secrets=secrets)
            logger.warning('model_call failed prompt_id=%s attempt=%d elapsed_ms=%d status=%d error_type=%s diagnostic_id=%s', prompt_id, attempt, int((time.monotonic() - start) * 1000), exc.response.status_code, type(exc).__name__, diagnostic_id)
            raise ModelServiceError(f'模型服务请求失败 (HTTP {exc.response.status_code})') from None
        except httpx.RequestError as exc:
            diagnostic_id = record_failure(kind='network_error', payload=payload, schema=json_schema, errors={'type': type(exc).__name__}, context=context, secrets=secrets)
            logger.warning('model_call failed prompt_id=%s attempt=%d elapsed_ms=%d error_type=%s diagnostic_id=%s', prompt_id, attempt, int((time.monotonic() - start) * 1000), type(exc).__name__, diagnostic_id)
            raise ModelServiceError('模型服务连接失败或超时，请检查设置') from None
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            errors = _safe_validation_errors(exc, json_schema) if isinstance(exc, ValidationError) else [{'type': 'invalid_response', 'path': []}]
            diagnostic_id = record_failure(kind='model_validation', payload=payload, schema=json_schema, response=content, errors=errors, context=context, secrets=secrets)
            logger.warning('model_call invalid prompt_id=%s attempt=%d errors=%s diagnostic_id=%s', prompt_id, attempt, json.dumps(errors, ensure_ascii=False), diagnostic_id)
            if attempt == 2:
                raise StructuredOutputError('模型返回的结构化结果无效') from None
            messages.append({'role': 'user', 'content': json.dumps({'validation_feedback': errors}, ensure_ascii=False)})
