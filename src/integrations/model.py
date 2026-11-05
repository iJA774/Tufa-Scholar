"""OpenAI-compatible structured chat client."""
import json
import logging
import time
from pathlib import Path
import httpx
from pydantic import ValidationError
from src import settings

logger = logging.getLogger(__name__)


def structured(prompt_id, payload, schema):
    config = settings.model_config()
    if not config.get('base_url') or not config.get('model'):
        raise RuntimeError('请先在设置中配置模型地址和模型名称')
    prompt = (Path(__file__).resolve().parents[2] / 'prompts' / (prompt_id + '.md')).read_text(encoding='utf-8')
    headers = {'Authorization': 'Bearer ' + config['api_key']} if config.get('api_key') else {}
    start = time.monotonic()
    logger.info('model_call started')
    try:
        response = httpx.post(config['base_url'].rstrip('/') + '/chat/completions', headers=headers,
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
