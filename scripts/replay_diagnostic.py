"""Validate a saved model response against the current prompt schema, offline."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
from src.integrations.model import StructuredOutputError


def replay(artifact):
    if not isinstance(artifact, dict) or not isinstance(artifact.get('context', {}), dict):
        return '诊断格式无效'
    if artifact.get('kind') != 'model_validation':
        return '只能诊断，无法离线复现'

    prompt_id = (artifact.get('context') or {}).get('prompt_id')
    try:
        from src.agents.literature import (
            AnalystOutput,
            BackgroundOutput,
            DispatcherOutput,
            ResearchActionOutput,
            _parse_control,
        )
        schemas = {
            'runtime/dispatcher': DispatcherOutput,
            'literature/background_investigator': BackgroundOutput,
            'literature/analyst': AnalystOutput,
        }
    except ImportError:
        return '无法离线复现：当前提示词 schema 不可用'
    schema = schemas.get(prompt_id)
    if prompt_id == 'literature/researcher':
        payload = artifact.get('payload')
        phase = payload.get('phase') if isinstance(payload, dict) else None
        schema = ResearchActionOutput if phase == 'act' else None
        if phase == 'draft':
            response = artifact.get('response')
            return '未复现' if isinstance(response, str) and response.strip() else '复现：文本为空或格式无效'
        if schema is None:
            return '无法离线复现：未知研究阶段'
    if prompt_id in ('literature/planner', 'literature/reporter'):
        response = artifact.get('response')
        return '未复现' if isinstance(response, str) and response.strip() else '复现：文本为空或格式无效'
    if schema is None:
        return '无法离线复现：未知提示词'
    try:
        response = artifact.get('response')
        if prompt_id == 'literature/analyst' and isinstance(response, str):
            _parse_control(response, schema)
        elif isinstance(response, str):
            schema.model_validate_json(response)
        else:
            schema.model_validate(response)
    except (ValueError, ValidationError, StructuredOutputError) as exc:
        from src.integrations.model import _safe_validation_errors

        errors = _safe_validation_errors(exc, schema.model_json_schema()) if isinstance(exc, ValidationError) else [{'type': 'invalid_control'}]
        return '复现：' + json.dumps(errors, ensure_ascii=False)
    return '未复现'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        artifact = json.loads(args.path.read_text(encoding='utf-8'))
        print('使用当前代码重放；脱敏或环境差异可能影响结果。')
        print(replay(artifact))
    except (OSError, ValueError, TypeError):
        print('无法读取诊断文件')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
