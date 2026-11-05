"""OpenAI-compatible structured chat client."""
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from pydantic import ValidationError
from src import settings

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


def discover(url, key):
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    try:
        catalog = bailian_url(url)
        response = httpx.get(base_url(url) + '/models', headers=headers, timeout=15, follow_redirects=False)
        if catalog and response.status_code in (404, 405):
            ids = []
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
                ids.extend(item['model'] for item in data if isinstance(item, dict) and isinstance(item.get('model'), str) and item['model'])
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
            ids = [item['id'] for item in data if isinstance(item, dict) and isinstance(item.get('id'), str) and item['id']]
        if key and any(key in model for model in ids):
            raise ValueError()
        return list(dict.fromkeys(ids))
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f'获取模型失败 (HTTP {exc.response.status_code})，请检查地址和密钥，或手动填写模型名称') from None
    except httpx.RequestError:
        raise RuntimeError('无法连接模型服务，请检查地址，或手动填写模型名称') from None
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('模型服务返回的数据无效，请手动填写模型名称') from None


def structured(prompt_id, payload, schema):
    config = settings.model_config()
    if not config.get('base_url') or not config.get('model'):
        raise RuntimeError('请先在设置中配置模型地址和模型名称')
    prompt = (Path(__file__).resolve().parents[2] / 'prompts' / (prompt_id + '.md')).read_text(encoding='utf-8')
    headers = {'Authorization': 'Bearer ' + config['api_key']} if config.get('api_key') else {}
    start = time.monotonic()
    logger.info('model_call started')
    try:
        response = httpx.post(base_url(config['base_url']) + '/chat/completions', headers=headers,
                              json={'model': config['model'], 'messages': [{'role': 'system', 'content': prompt},
                                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                                    'response_format': {'type': 'json_object'}}, timeout=90)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        result = schema.model_validate_json(content)
        logger.info('model_call completed elapsed_ms=%d status=%d', int((time.monotonic() - start) * 1000), response.status_code)
        return result
    except httpx.HTTPStatusError as exc:
        logger.warning('model_call failed elapsed_ms=%d status=%d error_type=%s', int((time.monotonic() - start) * 1000), exc.response.status_code, type(exc).__name__)
        raise RuntimeError(f'模型服务请求失败 (HTTP {exc.response.status_code})') from None
    except httpx.RequestError as exc:
        logger.warning('model_call failed elapsed_ms=%d error_type=%s', int((time.monotonic() - start) * 1000), type(exc).__name__)
        raise RuntimeError('模型服务连接失败或超时，请检查设置') from None
    except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
        logger.warning('model_call failed elapsed_ms=%d error_type=%s', int((time.monotonic() - start) * 1000), type(exc).__name__)
        raise RuntimeError('模型返回的结构化结果无效') from None
