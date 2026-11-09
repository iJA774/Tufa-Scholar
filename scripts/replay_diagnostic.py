"""Replay a single explicitly selected diagnostic without network or database access."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError


def replay(artifact):
    if not isinstance(artifact, dict) or not isinstance(artifact.get('context', {}), dict):
        return '诊断格式无效'
    kind = artifact.get('kind')
    response = artifact.get('response')
    context = artifact.get('context') or {}
    if kind == 'model_validation':
        from src.agents.literature import Route, Specialist, Review
        from src.agents.report import Draft, Verdict, PatchSet
        classes = {'runtime/route': Route, 'literature/research': Specialist,
                   'literature/review': Review, 'literature/synthesize': Draft,
                   'literature/revise': PatchSet, 'literature/verify_claim': Verdict}
        cls = classes.get(context.get('prompt_id'))
        if cls is None:
            return '无法离线复现：未知提示词'
        try:
            if isinstance(response, str):
                parsed = cls.model_validate_json(response)
            else:
                parsed = cls.model_validate(response)
        except ValidationError as exc:
            from src.integrations.model import _safe_validation_errors
            return '复现：' + json.dumps(_safe_validation_errors(exc, cls.model_json_schema()), ensure_ascii=False)
        if context.get('prompt_id') == 'literature/research' and parsed.action == 'read_web_page' and parsed.url and parsed.candidate_ids:
            payload = artifact.get('payload') or {}
            return replay({'kind': 'semantic_candidates', 'response': parsed.model_dump(), 'payload': payload, 'context': {'candidate_ids': [c.get('id') for c in payload.get('candidates', [])], 'role': payload.get('role')}})
        return '未复现'
    if kind == 'semantic_route':
        return '复现' if not isinstance(response, dict) or response.get('action') not in context.get('allowed_actions', []) else '未复现'
    if kind == 'semantic_candidates':
        ids = response.get('candidate_ids') if isinstance(response, dict) else None
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids): return '复现'
        if len(set(ids)) != len(ids) or any(i not in context.get('candidate_ids', []) for i in ids): return '复现'
        if not isinstance(response, dict) or response.get('action') not in ('read_web_page', 'search'):
            return '未复现'
        from src.agents.literature import Specialist, specialist_semantic_error
        try:
            choice = Specialist.model_validate(response)
        except ValidationError:
            return '复现'
        payload = artifact.get('payload') or {}
        role = context.get('role') or payload.get('role')
        if not role: return '无法离线复现：缺少来源上下文'
        if choice.url and not choice.candidate_ids:
            attempts = context.get('direct_url_attempts')
            if attempts is None:
                if payload.get('direct_url_attempts_remaining') == 2: attempts = []
                else: return '无法离线复现：缺少网址尝试记录'
            can_direct = context.get('can_read_direct_url')
            if can_direct is None:
                if payload.get('search_blocked'): can_direct = True
                elif payload.get('fallback_reason') and payload.get('candidate_count') == 0: can_direct = True
                else: return '无法离线复现：缺少直读范围信息'
        else:
            attempts, can_direct = [], False
        candidate_urls = {c.get('id'): c.get('url', '') for c in payload.get('candidates', [])}
        if choice.url and choice.candidate_ids and not candidate_urls.get(choice.candidate_ids[0]):
            return '无法离线复现：缺少候选网址'
        code = specialist_semantic_error(choice, kind=role, blocked=bool(context.get('search_blocked', payload.get('search_blocked'))),
            can_direct=can_direct, candidate_ids=set(context.get('candidate_ids', [])), direct_url_attempts=attempts, candidate_urls=candidate_urls)
        return '复现：' + code if code else '未复现'
    return '只能诊断，无法离线复现'


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
