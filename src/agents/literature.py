"""Evidence-based chat graph; external text is always passed as data."""
import re
import logging
import json
from urllib.parse import urlsplit, urlunsplit
from typing import TypedDict, Literal
from pydantic import model_validator, field_validator, ValidationError
from pydantic_core import PydanticCustomError
from src.literature.retrieval import _candidate_ids
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langgraph.errors import GraphRecursionError
from src.integrations.model import structured, StructuredOutputError, ModelServiceError
from src.integrations.web_search import WebSearchError
from src.integrations.web_page import WebPageError
from src.infrastructure.diagnostics import record_failure, diagnostic_context
from src.tools import ToolContext, ToolRegistry
from src.tools.registry import ToolCallValidationError
from src.literature.retrieval import rank_candidates
from src.literature.keywords import relevance_terms, relevance_score

logger = logging.getLogger('tufa.agent')
registry = ToolRegistry()
MAX_DELEGATIONS = 3
MAX_TOOL_CALLS = 8
MAX_SEARCH_RESULTS = 3
GRAPH_RECURSION_LIMIT = 30
MAX_DECISIONS = 3


def _run_id():
    return diagnostic_context().get('run_id', '-')


def _context(research):
    return ToolContext(research["project_id"], research["source"], tuple(research.get("paper_ids") or ()))


from src.agents.report import Claim, Draft, Section, Verdict, build_report_graph, render_partial, verify as _report_verify, render as _report_render

class State(TypedDict, total=False):
    tool_feedback: dict
    tool_validation_failures: int
    review_failed: bool
    retained_verified: dict
    recursion_exhausted: bool
    research: dict
    effective_source: str
    directions: list
    pools: dict
    attempted_plans: list
    excluded_evidence_ids: list
    read_ids: list
    executed_queries: list
    web_search_blocked: str
    web_failures: int
    web_backend: str
    web_empty_count: int
    fallback_reason: str
    auto_web: bool
    subtask_notes: list
    direct_url_attempts: list
    plan: list
    web_blocks: dict
    web_read_failures: list
    verification_gaps: list
    verification_attempted: bool
    feedback_pending: bool
    feedback_actions: list
    feedback_goal: str
    history: list
    queries: list
    evidence_query: str
    candidates: list
    papers: list
    query_results: dict
    new_evidence: bool
    evidence: list
    evidence_pool: list
    evidence_facets: list
    draft: Draft
    content: str
    warnings: list
    candidate_identities: list
    task: str
    choice: object
    tool_calls: int
    recent_result: str
    search_status: str
    delegations: int
    review_result: object
    results: list
    route: object
    decisions: int
    forced_reason: str
    verified_content: str
    verified_evidence: list
    rejected: bool
    revised: bool
    rejection_feedback: list


def _model(state, prompt_id, payload, schema):
    research = state['research']
    if research.get('model_name') or research.get('reasoning_effort'):
        return structured(prompt_id, payload, schema, model_options=research)
    return structured(prompt_id, payload, schema)



def _direct_url_key(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None
                or '?' in url or '#' in url or parts.port not in (None, 80 if parts.scheme.lower() == 'http' else 443)):
            return ''
        hostname = parts.hostname.lower()
        host = f'[{hostname}]' if ':' in hostname else hostname
        return urlunsplit((parts.scheme.lower(), host, parts.path or '/', '', ''))
    except ValueError:
        return ''



def _can_read_direct(kind, state, candidate_count):
    if kind != 'web': return False
    return bool(state.get('web_search_blocked') or
                (state.get('auto_web') and candidate_count == 0 and
                 (state.get('fallback_reason') or state.get('web_failures', 0) or state.get('web_empty_count', 0))))


def specialist_semantic_error(choice, *, kind, blocked, can_direct, candidate_ids, direct_url_attempts, candidate_urls=None):
    if choice.action == 'search' and kind == 'web' and blocked: return 'search_blocked'
    if choice.action == 'read_web_page' and choice.url and choice.candidate_ids:
        known = (candidate_urls or {}).get(choice.candidate_ids[0], '')
        same = bool(known and (choice.url == known or (_direct_url_key(choice.url) and _direct_url_key(choice.url) == _direct_url_key(known))))
        if kind != 'web' or not same: return 'conflicting_candidate_url'
    elif choice.action == 'read_web_page' and choice.url:
        key = _direct_url_key(choice.url)
        if key and key in direct_url_attempts: return 'repeated_direct_url'
        if not (kind == 'web' and can_direct and key and choice.offset == 0 and key not in direct_url_attempts and len(direct_url_attempts) < 2):
            return 'invalid_direct_url'
    if choice.action == 'read_web_page' and kind != 'web': return 'invalid_candidate_ids'
    if choice.action in ('read', 'read_web_page') and (len(set(choice.candidate_ids)) != len(choice.candidate_ids) or any(i not in candidate_ids for i in choice.candidate_ids)):
        return 'invalid_candidate_ids'
    return ''

def _web_actionable(s):
    if not s.get('web_search_blocked'):
        return True
    pool = s.get('pools', {}).get('web_research', {})
    failures = {item.get('candidate_id'): item for item in pool.get('web_read_failures', [])}
    has_page = any(block.get('next_offset') is not None and not (
        failures.get(key) and not (failures[key].get('retryable') and failures[key].get('attempts', 0) < 2)
    ) for key, blocks in pool.get('web_blocks', {}).items() for block in blocks[-1:])
    pending = len(pool.get('candidate_identities', [])) > len(pool.get('read_ids', [])) or has_page
    last = next((item for item in reversed(s.get('results', [])) if item.get('route') == 'web_research'), {})
    return pending or (len(s.get('direct_url_attempts', [])) < 2 and last.get('tool_calls') != 0)

def search(s):
    research = s['research']
    query_results = {key: list(items) for key, items in s.get('query_results', {}).items()}
    warnings = list(s.get('warnings', []))
    queries = [' '.join(s['queries'])] if research['source'] == 'collection' else s['queries']
    succeeded, returned_rows = False, 0
    failures, blocked = s.get('web_failures', 0), s.get('web_search_blocked', '')
    backend, empties = s.get('web_backend', 'primary'), s.get('web_empty_count', 0)
    fallback_reason = s.get('fallback_reason', '')
    for query in queries:
        if research['source'] == 'web' and blocked: break
        key = query.strip().casefold()
        try:
            if research['source'] == 'web':
                while True:
                    try:
                        args = {'query': query, 'limit': MAX_SEARCH_RESULTS}
                        if backend == 'alternative': args['backend'] = 'alternative'
                        results = [item for item in registry.invoke('search_web', args, context=_context(research)) if item.get('url')][:MAX_SEARCH_RESULTS]
                        failures = 0
                        empties = empties + 1 if not results and not any(query_results.values()) else 0
                        if backend == 'alternative' and results:
                            warnings = [warning for warning in warnings if warning != 'web_search_failed']
                        if s.get('auto_web') and empties >= 2 and not results:
                            if backend == 'primary':
                                backend, empties, fallback_reason = 'alternative', 0, 'empty'
                                logger.info('web_search_fallback activated reason=%s', fallback_reason)
                                continue
                            blocked = 'empty'
                        break
                    except WebSearchError as exc:
                        category = exc.category if exc.category in {'configuration', 'missing_key', 'challenge', 'rate_limit', 'auth', 'http_status', 'timeout', 'network', 'invalid_response', 'provider_error'} else 'provider_error'
                        failures += 1
                        hard = category in {'configuration', 'missing_key', 'challenge', 'rate_limit', 'auth'} or (failures >= 2 and (category in {'timeout', 'network', 'invalid_response', 'provider_error'} or category == 'http_status' and (exc.status or 0) >= 500))
                        if hard and s.get('auto_web') and backend == 'primary':
                            backend, failures, empties, fallback_reason = 'alternative', 0, 0, category
                            logger.info('web_search_fallback activated reason=%s', fallback_reason)
                            continue
                        if hard:
                            blocked = category
                            warnings.append('web_search_blocked:' + category)
                        warnings.append('web_search_failed')
                        break
                if failures: continue
            else:
                result = registry.invoke('search_papers', {'query': query}, context=_context(research))
                warnings.extend(result.get('warnings', []))
                results = result['results'] if research['source'] == 'collection' else result['results'][:MAX_SEARCH_RESULTS]
            succeeded = True
            returned_rows += len(results)
        except RuntimeError:
            if research['source'] != 'web': raise
            warnings.append('web_search_failed')
            continue
        entries = query_results.setdefault(key, [])
        for position, paper in enumerate(results, 1):
            rank = paper.get('rank')
            if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1: rank = position
            entries.append({'paper': paper, 'rank': rank})
    candidates = rank_candidates(query_results, research['source'])
    if research['source'] == 'web' and not succeeded and not query_results and not candidates and not blocked and failures == 0:
        raise RuntimeError('网页检索失败，无法获取证据，请检查搜索设置或稍后重试。')
    return {'candidates': candidates, 'papers': candidates[:6], 'query_results': query_results, 'warnings': warnings,
            'search_status': 'failed' if not succeeded else ('results' if returned_rows else 'empty'), 'web_search_blocked': blocked,
            'web_failures': failures, 'web_backend': backend, 'web_empty_count': empties, 'fallback_reason': fallback_reason}


def _evidence_signature(entry):
    url = entry.get('original_url') or ''
    if url:
        parts = urlsplit(url)
        url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))
    return tuple(entry.get(field) for field in ('paper_id', 'version_id', 'page', 'title', 'source_level', 'text')) + (url,)


# Bound both the number of entries and text sent to a model.
EVIDENCE_LIMIT = 18
EVIDENCE_TEXT_LIMIT = 21600


def _select_evidence(pool, facets):
    terms = [relevance_terms(facet) for facet in facets]
    texts = [(entry.get('title', '') + ' ' + entry.get('text', '')).casefold() for entry in pool]
    scores = [[relevance_score(facet, text) for text in texts] for facet in terms]
    selected = []
    size = 0

    def add(index):
        nonlocal size
        length = len(pool[index].get('text', ''))
        if index not in selected and len(selected) < EVIDENCE_LIMIT and size + length <= EVIDENCE_TEXT_LIMIT:
            selected.append(index)
            size += length

    for facet_scores in scores:
        for index in sorted(range(len(pool)), key=lambda i: (-facet_scores[i], i)):
            if not facet_scores[index]:
                break
            add(index)
            if index in selected:
                break
    for index in sorted(range(len(pool)), key=lambda i: (-sum(row[i] for row in scores), i)):
        if sum(row[index] for row in scores) > 0:
            add(index)
    return [pool[index] for index in selected]



def _plan_signature(route, goal):
    return (route, ' '.join(goal.casefold().split()))


def _allowed_routes(source):
    return ['paper_research'] if source in ('local', 'collection') else ['paper_research', 'web_research']


def _relevance_queries(question, query, executed):
    # Reserve space for both actual searches and an original-question excerpt.
    searches = list(dict.fromkeys(value.strip() for value in reversed(executed) if value.strip()))[:4]
    parts = [question[i:i+1000].strip() for i in range(0, len(question), 1000)]
    return list(dict.fromkeys(searches + [part for part in parts if part]))[:8]


def evidence(s):
    query = s.get('evidence_query') or s['research']['query']
    found = registry.invoke(
        'collect_evidence', {'query': query, 'papers': s['papers'], 'relevance_queries': _relevance_queries(s['research']['query'], query, s.get('executed_queries', []))}, context=_context(s['research'])
    )
    pool = list(s.get('evidence_pool', []))
    signatures = {_evidence_signature(item) for item in pool}
    ids = {item['id'] for item in pool}
    new_evidence = False
    for item in found:
        signature = _evidence_signature(item)
        if signature in signatures:
            continue
        entry = dict(item)
        base = entry['id']
        suffix = 2
        while entry['id'] in ids:
            entry['id'] = f'{base}_{suffix}'
            suffix += 1
        ids.add(entry['id'])
        signatures.add(signature)
        pool.append(entry)
        new_evidence = True
    facets = list(s.get('evidence_facets', [s['research']['query']]))
    if query.strip() and query.strip().casefold() not in {q.strip().casefold() for q in facets}:
        facets.append(query)
    facets = list(dict.fromkeys(facets + s.get('executed_queries', [])))
    excluded = set(s.get('excluded_evidence_ids', []))
    return {'evidence': _select_evidence([item for item in pool if item['id'] not in excluded], facets), 'evidence_pool': pool,
            'evidence_facets': facets, 'new_evidence': new_evidence}


def verify(s):
    checked = _report_verify(s, model=_model)
    return {**checked, **_report_render({**s, **checked})}


class Route(BaseModel):
    action: Literal['direct', 'paper_research', 'web_research', 'review', 'report', 'partial']
    task: str = Field(default='', max_length=1000)
    answer: str = Field(default='', max_length=12000)
    directions: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def validate_action(self):
        if self.action in ('paper_research', 'web_research') and (not self.directions or any(not d.strip() or len(d) > 500 for d in self.directions)):
            raise ValueError('研究方向不能为空')
        if self.action in ('paper_research', 'web_research') and not self.task.strip():
            raise ValueError('委派任务不能为空')
        if self.action == 'direct' and not self.answer.strip():
            raise ValueError('直接回答不能为空')
        return self


class Specialist(BaseModel):
    action: Literal['search', 'read', 'read_web_page', 'finish']
    query: str = Field(default='', max_length=1000)
    candidate_ids: list[str] = Field(default_factory=list, max_length=6)
    plan: list[str] = Field(min_length=1, max_length=6)
    url: str = Field(default='', max_length=2000)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=1000, ge=1, le=4000)

    @field_validator('plan', mode='before')
    @classmethod
    def validate_plan(cls, value):
        if not isinstance(value, list) or not value or len(value) > 6 or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in value):
            raise PydanticCustomError('specialist_plan_invalid', '研究计划不能为空')
        return value

    @model_validator(mode='after')
    def validate_action(self):
        if self.action == 'search' and not self.query.strip():
            raise PydanticCustomError('specialist_search_query_required', '搜索查询不能为空')
        if self.action == 'read_web_page' and not self.candidate_ids and self.url and not self.query.strip():
            raise PydanticCustomError('specialist_direct_query_required', '直读网址需提供主题词')
        if self.action == 'read_web_page' and not (len(self.candidate_ids) == 1 or (not self.candidate_ids and self.url and self.query.strip())):
            raise PydanticCustomError('specialist_web_target_required', '读取网页须指定一个候选或网址')
        if self.action == 'read' and not self.candidate_ids:
            raise PydanticCustomError('specialist_read_candidates_required', '精读候选不能为空')
        return self


class NextStep(BaseModel):
    direction: str = Field(min_length=1, max_length=500)
    goal: str = Field(min_length=1, max_length=1000)
    route: Literal['paper_research', 'web_research']


class SubtaskFinding(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    citations: list[str] = Field(min_length=1, max_length=6)


class Review(BaseModel):
    sufficient: bool
    gaps: list[str] = Field(default_factory=list, max_length=10)
    summary: str = Field(max_length=2000)
    coverage: list[str] = Field(default_factory=list, max_length=10)
    next_steps: list[NextStep] = Field(default_factory=list, max_length=6)
    irrelevant_evidence_ids: list[str] = Field(default_factory=list, max_length=18)
    subtask_findings: list[SubtaskFinding] = Field(default_factory=list, max_length=3)
    subtask_gap: str = Field(default='', max_length=400)

    @field_validator('subtask_findings', mode='before')
    @classmethod
    def clean_findings(cls, value):
        if not isinstance(value, list): return []
        valid = []
        for item in value:
            try:
                finding = SubtaskFinding.model_validate(item)
                if finding.text.strip() and all(c.strip() for c in finding.citations): valid.append(finding)
            except (ValidationError, TypeError, ValueError):
                continue
            if len(valid) == 3: break
        return valid

    @model_validator(mode='after')
    def validate_gaps(self):
        if any(len(gap) > 1000 for gap in self.gaps) or any(not item.strip() or len(item) > 500 for item in self.coverage):
            raise ValueError('审查缺口过长')
        return self


def _summary_evidence(s):
    return [{**{key: e.get(key) for key in ('id', 'title', 'source_level', 'paper_id', 'version_id', 'page', 'original_url', 'provider', 'retrieved_at', 'char_start', 'char_end')}, 'text': e.get('text', '')[:600]} for e in s.get('evidence', [])]


def _candidate_views(s):
    # IDs are assigned once, independently of changing RRF order.
    identities = [{'id': item['id'], 'keys': set(item['keys']), 'title': item['title']} for item in s.get('candidate_identities', [])]
    views = []
    for paper in s.get('candidates', []):
        keys = _candidate_ids(paper, s['research']['source'])
        title = (paper.get('title') or '').strip().casefold()
        match = next((item for item in identities if keys & item['keys'] or (not keys and not item['keys'] and title and title == item['title'])), None)
        if match is None:
            match = {'id': f'C{len(identities) + 1}', 'keys': set(), 'title': title}
            identities.append(match)
        match['keys'].update(keys)
        views.append((match['id'], paper))
    return views, identities


class ResearchToolFailure(RuntimeError):
    pass


def _research_graph(kind, on_stage):
    def decide(s):
        views, identities = _candidate_views(s)
        allowed = {'search_web' if kind == 'web' else 'search_papers', 'collect_evidence'}
        if kind == 'web':
            allowed.add('read_web_page')
            if s.get('web_search_blocked'): allowed.discard('search_web')
        tools = [tool for tool in registry.list_tools(_context(s['research'])) if tool['name'] in allowed]
        can_direct = _can_read_direct(kind, s, len(views))
        direct_reason = ('blocked' if s.get('web_search_blocked') else 'search_unavailable' if can_direct else '')
        payload = {
            'can_read_direct_url': can_direct, 'direct_url_reason': direct_reason, 'task': s['task'], 'verification_gaps': s.get('verification_gaps', []), 'directions': s.get('directions', []), 'plan': s.get('plan', []), 'review': s['review_result'].model_dump() if s.get('review_result') else None, 'results': s.get('results', []), 'role': kind, 'source': s['research']['source'], 'tools': tools,
            'candidates': [{'id': key, 'title': p.get('title', ''), 'summary': (p.get('abstract') or p.get('content') or '')[:500], 'doi': p.get('doi'), 'url': p.get('url'), 'collected': key in s.get('read_ids', []), 'next_offset': (s.get('web_blocks', {}).get(key) or [{}])[-1].get('next_offset')} for key, p in views[:20]],
            'candidate_count': len(views), 'recent_result': s.get('recent_result', ''), 'search_status': s.get('search_status', 'not_searched'),
            'web_blocks': [{'candidate_id': key, **{k: block.get(k) for k in ('offset', 'next_offset', 'markdown')}} for key, blocks in s.get('web_blocks', {}).items() for block in blocks[-1:]][-6:],
            'web_read_failures': s.get('web_read_failures', []),
            'evidence': _summary_evidence(s), 'calls_remaining': MAX_TOOL_CALLS - s.get('tool_calls', 0), 'search_blocked': s.get('web_search_blocked', ''), 'search_backend': s.get('web_backend', 'primary'), 'fallback_reason': s.get('fallback_reason', ''), 'empty_searches': s.get('web_empty_count', 0), 'prior_subtask_notes': s.get('subtask_notes', [])[-10:], 'direct_url_attempts_remaining': 2-len(s.get('direct_url_attempts', [])), 'attempted_direct_urls': s.get('direct_url_attempts', []),
        }
        if s.get('tool_feedback'): payload['validation_feedback'] = s['tool_feedback']
        valid_ids = {key for key, _ in views}
        for attempt in range(2):
            try:
                choice = _model(s, 'literature/research', payload, Specialist)
            except StructuredOutputError:
                return {'choice': Specialist(action='finish', plan=['结束']), 'recent_result': '研究决策格式无效，已结束本轮'}
            except ModelServiceError:
                if not (s.get('evidence') or s.get('evidence_pool')): raise
                return {'choice': Specialist(action='finish', plan=['结束']), 'recent_result': '模型服务不可用，已结束本轮', 'review_failed': True}
            logger.info('prompt_id=literature/research action=%s tool_calls=%d attempt=%d', choice.action, s.get('tool_calls', 0), attempt + 1)
            code = specialist_semantic_error(choice, kind=kind, blocked=bool(s.get('web_search_blocked')),
                can_direct=can_direct, candidate_ids=valid_ids, direct_url_attempts=s.get('direct_url_attempts', []), candidate_urls={key: item.get('url', '') for key, item in views})
            if not code and choice.action == 'read_web_page' and choice.candidate_ids:
                failed_pages = [item for item in s.get('web_read_failures', []) if item.get('candidate_id') == choice.candidate_ids[0]]
                retry = next((item for item in failed_pages if item.get('offset') == choice.offset), None)
                if failed_pages and not (retry and retry.get('retryable') and retry.get('attempts', 0) < 2):
                    code = 'repeated_web_page_failure'
                    failure = retry or failed_pages[-1]
                    logger.info('research_web_page run_id=%s candidate_id=%s offset=%d limit=%d outcome=repeated_failure_rejected category=%s reason=%s status=%s',
                                _run_id(), choice.candidate_ids[0], choice.offset, choice.limit,
                                failure.get('category', 'reader_error'), failure.get('reason', 'reader_error'),
                                failure.get('status') if failure.get('status') is not None else '-')
            if not code:
                if choice.action == 'read_web_page' and choice.candidate_ids and choice.url:
                    choice = choice.model_copy(update={'url': '', 'query': ''})
                break
            if choice.action == 'read_web_page' and code != 'repeated_web_page_failure':
                rejected_id = (choice.candidate_ids[0] if choice.candidate_ids and choice.candidate_ids[0] in valid_ids
                               else 'direct' if not choice.candidate_ids else 'unknown')
                reason = 'previously_attempted' if code == 'repeated_direct_url' else 'validation_rejected'
                logger.info('research_web_page run_id=%s candidate_id=%s offset=%d limit=%d outcome=rejected category=%s reason=%s status=-',
                            _run_id(), rejected_id, choice.offset, choice.limit, code, reason)
            if code != 'repeated_web_page_failure':
                diagnostic_id = record_failure(kind='semantic_candidates', payload=payload, schema=Specialist.model_json_schema(),
                    response=choice.model_dump(), errors=[code],
                    context={'candidate_ids': sorted(valid_ids), 'prompt_id': 'literature/research', 'attempt': attempt + 1, 'role': kind, 'search_blocked': bool(s.get('web_search_blocked')), 'can_read_direct_url': can_direct, 'direct_url_attempts': s.get('direct_url_attempts', [])})
                logger.warning('prompt_id=literature/research diagnostic_id=%s', diagnostic_id)
            if attempt:
                reason = '网页正文读取已失败且不应重复请求，已结束本轮' if code == 'repeated_web_page_failure' else '工具选择无效，已结束本轮'
                return {'choice': Specialist(action='finish', plan=choice.plan), 'candidate_identities': identities, 'plan': choice.plan, 'recent_result': reason}
            instruction = ('该网址已尝试，请选择其他可信网址或结束' if code == 'repeated_direct_url' else
                           '候选分页只传 candidate_ids，省略 url 和 query' if code == 'conflicting_candidate_url' else
                           '该候选正文读取已失败；使用其搜索摘要、其他候选，或对该候选执行证据提取' if code == 'repeated_web_page_failure' else '')
            payload = {**payload, 'validation_feedback': {'code': code, 'allowed_ids': [key for key, _ in views[:20]], 'instruction': instruction, 'can_read_direct_url': can_direct, 'direct_url_reason': direct_reason}}
        return {'choice': choice, 'candidate_identities': identities, 'plan': choice.plan}

    def invoke_core(s):
        choice = s['choice']
        if choice.action == 'search':
            if on_stage: on_stage('searching_web' if kind == 'web' else 'searching')
            try:
                update = search({**s, 'queries': [choice.query]})
            except RuntimeError as exc:
                raise ResearchToolFailure() from exc
            update['executed_queries'] = list(dict.fromkeys(s.get('executed_queries', []) + ([choice.query] if update['search_status'] != 'failed' else [])))[-7:]
            status = update['search_status']
            result = ('本次网页检索失败；保留此前结果' if status == 'failed' else
                      '本次检索成功但没有返回结果；仍可使用此前候选' if status == 'empty' else f"检索到 {len(update['candidates'])} 个累计候选")
        elif choice.action == 'read_web_page':
            if on_stage: on_stage('searching_web')
            mapping = dict(_candidate_views(s)[0])
            candidate = mapping[choice.candidate_ids[0]] if choice.candidate_ids else {'url': choice.url, 'title': choice.url}
            attempts = list(s.get('direct_url_attempts', []))
            if choice.url: attempts.append(_direct_url_key(choice.url))
            blocks = {key: list(value) for key, value in s.get('web_blocks', {}).items()}
            failures = [dict(item) for item in s.get('web_read_failures', [])]
            candidate_id = choice.candidate_ids[0] if choice.candidate_ids else ''
            log_candidate_id = candidate_id or 'direct'
            prior_failure = next((item for item in failures if item.get('candidate_id') == candidate_id and item.get('offset') == choice.offset), None) if candidate_id else None
            try:
                block = registry.invoke('read_web_page', {'url': candidate['url'], 'offset': choice.offset, 'limit': choice.limit}, context=_context(s['research']))
                if candidate_id:
                    failures = [item for item in failures if item.get('candidate_id') != candidate_id]
                markdown = block.get('markdown', '')
                chars = len(markdown) if isinstance(markdown, str) else 0
                if isinstance(markdown, str) and markdown.strip():
                    if choice.url:
                        direct_candidate = {'url': block['url'], 'title': block['title'] or choice.url, 'content': '', 'source_level': 'web_content', 'provider': 'web_page', 'retrieved_at': block['retrieved_at']}
                        candidates = list(s.get('candidates', []))
                        matching = next((i for i, item in enumerate(candidates) if _candidate_ids(item, 'web') & _candidate_ids(direct_candidate, 'web')), None)
                        if matching is None:
                            candidates.append(direct_candidate)
                            matching = len(candidates) - 1
                        views, identities = _candidate_views({**s, 'candidates': candidates})
                        candidate_id = views[matching][0]
                    else:
                        candidates, identities, candidate_id = s.get('candidates', []), s.get('candidate_identities', []), choice.candidate_ids[0]
                    entries = blocks.setdefault(candidate_id, [])
                    entries[:] = [item for item in entries if item['offset'] != block['offset']]
                    entries.append(block)
                    result = '网页正文块已读取'
                    outcome = 'success'
                else:
                    result = '网页正文为空或已到末尾'
                    outcome = 'empty'
                logger.info('research_web_page run_id=%s candidate_id=%s offset=%d limit=%d outcome=%s chars=%d next_offset=%s',
                            _run_id(), log_candidate_id, choice.offset, choice.limit, outcome, chars,
                            block.get('next_offset') if block.get('next_offset') is not None else '-')
                update = {'web_blocks': blocks, 'web_read_failures': failures, 'direct_url_attempts': attempts}
                if isinstance(markdown, str) and markdown.strip() and choice.url:
                    update.update(candidates=candidates, candidate_identities=identities, executed_queries=list(dict.fromkeys(s.get('executed_queries', []) + [choice.query]))[-7:])
            except RuntimeError as exc:
                category = exc.category if isinstance(exc, WebPageError) else 'reader_error'
                reason = exc.reason if isinstance(exc, WebPageError) else category
                status = getattr(exc, 'status', None) if isinstance(exc, WebPageError) else None
                if isinstance(status, bool) or not isinstance(status, int): status = None
                retryable = category in {'connect_error', 'timeout'} or category == 'http_status' and isinstance(status, int) and status >= 500
                attempts_used = (prior_failure.get('attempts', 0) if prior_failure else 0) + 1
                failure = {'candidate_id': candidate_id, 'offset': choice.offset, 'category': category, 'reason': reason, 'status': status,
                           'attempts': attempts_used, 'retryable': retryable and attempts_used < 2}
                if candidate_id:
                    failures = [item for item in failures if item.get('candidate_id') != candidate_id]
                    failures.append(failure)
                update = {'web_blocks': blocks, 'web_read_failures': failures,
                          'warnings': s.get('warnings', []) + ['web_page_read_failed'], 'direct_url_attempts': attempts}
                position = f'候选 {candidate_id} 的 offset={choice.offset}' if candidate_id else f'直读网页的 offset={choice.offset}'
                if status is not None: category = f'{category} (HTTP {status})'
                retry_hint = '可在当前工具预算内重试一次' if failure['retryable'] else '不应再次请求该页'
                result = f'网页正文读取失败，位置：{position}；原因：{category}；安全原因：{reason}；{retry_hint}。可使用候选摘要或其他候选'
                logger.warning('research_web_page run_id=%s candidate_id=%s offset=%d limit=%d outcome=failed category=%s reason=%s status=%s',
                               _run_id(), log_candidate_id, choice.offset, choice.limit, failure['category'], reason,
                               status if status is not None else '-')
        else:
            if on_stage: on_stage('evidence')
            mapping = dict(_candidate_views(s)[0])
            if len(set(choice.candidate_ids)) != len(choice.candidate_ids) or any(i not in mapping for i in choice.candidate_ids):
                raise ToolCallValidationError('工具参数无效', 'invalid_arguments', [{'type': 'value_error', 'path': ['candidate_ids']}])
            papers = []
            extras = []
            for candidate_id in choice.candidate_ids:
                candidate = mapping[candidate_id]
                blocks = s.get('web_blocks', {}).get(candidate_id, []) if kind == 'web' else []
                converted = [{'url': block['url'], 'title': block['title'] or candidate.get('title', ''),
                              'content': block['markdown'], 'source_level': 'web_content', 'provider': 'web_page',
                              'retrieved_at': block['retrieved_at'], 'content_offset': block['offset']}
                             for block in reversed(blocks) if block.get('markdown', '').strip()]
                papers.append(converted[0] if converted else candidate)
                extras.extend(converted[1:])
            papers.extend(extras[:6-len(papers)])
            update = evidence({**s, 'papers': papers, 'evidence_query': s['task']})
            update['read_ids'] = list(set(s.get('read_ids', [])) | set(choice.candidate_ids))
            result = f"本次新增证据：{update['new_evidence']}"
        return {**update, 'tool_calls': s.get('tool_calls', 0) + 1, 'recent_result': result}

    def invoke(s):
        try:
            return {**invoke_core(s), 'tool_feedback': {}, 'tool_validation_failures': 0}
        except ToolCallValidationError as exc:
            diagnostic_id = record_failure(kind='tool_validation', payload={}, schema={}, errors={'code': exc.code, 'errors': exc.errors}, context={'prompt_id': 'literature/research', 'role': kind, 'action': s['choice'].action})
            logger.warning('tool_validation role=%s action=%s code=%s errors=%s diagnostic_id=%s', kind, s['choice'].action, exc.code, json.dumps(exc.errors, ensure_ascii=False), diagnostic_id)
            failures = s.get('tool_validation_failures', 0) + 1
            return {'tool_calls': s.get('tool_calls', 0) + 1, 'tool_validation_failures': failures,
                    'tool_feedback': {'code': exc.code, 'errors': exc.errors},
                    'recent_result': '工具参数无效，已结束本轮' if failures >= 2 else '工具参数无效，请修正'}

    graph = StateGraph(State)
    graph.add_node('decide', decide)
    graph.add_node('invoke', invoke)
    graph.add_edge(START, 'decide')
    graph.add_conditional_edges('decide', lambda s: END if s['choice'].action == 'finish' or s.get('tool_calls', 0) >= MAX_TOOL_CALLS else 'invoke')
    graph.add_conditional_edges('invoke', lambda s: END if s['tool_calls'] >= MAX_TOOL_CALLS or s.get('tool_validation_failures', 0) >= 2 or (s.get('search_status') == 'failed' and not s.get('candidates') and not s.get('web_search_blocked') and not (kind == 'web' and s.get('auto_web') and s.get('web_backend') == 'alternative' and s.get('fallback_reason'))) else 'decide')
    return graph.compile()


def _stream_bounded(graph, state):
    latest = dict(state)
    try:
        for latest in graph.stream(state, {'recursion_limit': GRAPH_RECURSION_LIMIT}, stream_mode='values'):
            pass
    except GraphRecursionError:
        return {**latest, 'recursion_exhausted': True}
    return latest

def run(research: dict, *, history: list, on_stage=None, on_subtask=None):
    if research['source'] == 'collection' and not research.get('paper_ids'):
        return '证据不足：所选收藏夹没有文献。', []
    paper_graph = _research_graph('paper', on_stage)
    web_graph = _research_graph('web', on_stage)

    def supervisor(s):
        if on_stage: on_stage('planning')
        count = s.get('delegations', 0)
        if s.get('review_failed') or s.get('recursion_exhausted'):
            return {'route': Route(action='partial'), 'forced_reason': '审查或研究未能完成'}
        reviewed = s.get('review_result')
        exhausted = count >= MAX_DELEGATIONS or s.get('decisions', 0) >= MAX_DECISIONS
        effective = s.get('effective_source')
        source = research['source']
        if count and not reviewed:
            return {'route': Route(action='review')}
        if count and reviewed and not s.get('feedback_pending'):
            if reviewed.sufficient and not reviewed.gaps and s.get('evidence'):
                return {'route': Route(action='report')}
            permitted = _allowed_routes(source)
            attempted = [tuple(item) for item in s.get('attempted_plans', [])]
            def viable(step):
                signature = _plan_signature(step.route, step.goal)
                times = attempted.count(signature)
                latest = next((item for item in reversed(s.get('results', [])) if _plan_signature(item.get('route', ''), item.get('task', '')) == signature), {})
                return times == 0 or (times == 1 and latest.get('continuation_available', False))
            steps = [step for step in reviewed.next_steps if step.route in permitted and step.goal.strip() and step.direction.strip() and viable(step)]
            if not _web_actionable(s):
                steps = [step for step in steps if step.route != 'web_research']
            if exhausted or not steps:
                logger.info('research_partial reason=%s delegations=%d decisions=%d attempted=%d', 'budget' if exhausted else ('web_search_blocked' if s.get('web_search_blocked') else 'no_plan'), count, s.get('decisions', 0), len(attempted))
                return {'route': Route(action='partial'), 'forced_reason': '研究预算已耗尽' if exhausted else ('网页搜索连续未找到结果，已停止重复搜索' if s.get('web_search_blocked') == 'empty' else '网页搜索服务暂不可用，已暂停重复请求' if s.get('web_search_blocked') else '没有可执行的补研计划')}
            step = steps[0]
            return {'route': Route(action=step.route, task=step.goal, directions=[step.direction]), 'decisions': s.get('decisions', 0) + 1}
        if exhausted:
            return {'route': Route(action='partial'), 'forced_reason': '研究预算已耗尽'}
        if s.get('feedback_pending'):
            return {'route': Route(action=s['feedback_actions'][0], task=s['feedback_goal'], directions=[s['feedback_goal'][:500]]), 'decisions': s.get('decisions', 0) + 1}
        else:
            actions = (['paper_research', 'web_research'] if source == 'auto' else
                       ['web_research'] if source == 'web' else ['paper_research'])
            if source in ('auto', 'local'): actions.append('direct')
        payload = {'question': research['query'], 'history': s['history'], 'source': research['source'],
            'available_actions': actions, 'effective_source': effective, 'directions': s.get('directions', []), 'results': s.get('results', [])[-3:], 'evidence': _summary_evidence(s),
            'review': reviewed.model_dump() if reviewed else None, 'verification_gaps': s.get('verification_gaps', []), 'delegations_remaining': max(0, MAX_DELEGATIONS-count),
            'decisions_remaining': max(0, MAX_DECISIONS-s.get('decisions', 0))}
        for attempt in range(2):
            try:
                route = _model(s, 'runtime/route', payload, Route)
            except StructuredOutputError:
                return {'route': Route(action='partial'), 'forced_reason': '主控决策格式无效'}
            logger.info('prompt_id=runtime/route action=%s allowed=%s delegations=%d decisions=%d attempt=%d', route.action, actions, count, s.get('decisions', 0), attempt + 1)
            if route.action in actions:
                break
            diagnostic_id = record_failure(kind='semantic_route', payload=payload, schema=Route.model_json_schema(),
                response=route.model_dump(), errors=['invalid_action'],
                context={'allowed_actions': actions, 'prompt_id': 'runtime/route', 'attempt': attempt + 1})
            logger.warning('prompt_id=runtime/route diagnostic_id=%s', diagnostic_id)
            if attempt:
                return {'route': Route(action='partial'), 'forced_reason': '主控选择不符合当前范围'}
            payload = {**payload, 'validation_feedback': {'code': 'invalid_action', 'allowed_actions': actions}}
        return {'route': route, 'decisions': s.get('decisions', 0) + 1}

    def delegate(s):
        before = {_evidence_signature(e) for e in s.get('evidence_pool', [])}
        action = s['route'].action
        effective = 'web' if action == 'web_research' else (research['source'] if research['source'] in ('local', 'collection', 'openalex') else 'all')
        pools = dict(s.get('pools', {}))
        prior = pools.get(action, {})
        scoped_research = {**research, 'source': effective}
        directions = list(dict.fromkeys(s.get('directions', []) + s['route'].directions))
        input_state = {**s, **prior, 'research': scoped_research, 'directions': directions,
                       'auto_web': research['source'] == 'auto' and effective == 'web', 'task': s['route'].task, 'tool_calls': 0, 'recent_result': '', 'search_status': 'not_searched', 'tool_feedback': {}, 'tool_validation_failures': 0, 'executed_queries': list(prior.get('executed_queries', [])), 'plan': []}
        try:
            result = _stream_bounded(web_graph if action == 'web_research' else paper_graph, input_state)
        except ResearchToolFailure as exc:
            if not before and not prior.get('candidates') and not (research['source'] == 'auto' and effective == 'web'):
                raise exc.__cause__ from None
            result = {**input_state, 'tool_calls': 1, 'search_status': 'failed', 'warnings': s.get('warnings', []) + [('web_search_failed' if effective == 'web' else 'paper_search_failed')]}
        pools[action] = {key: result.get(key, default) for key, default in [('candidates', []), ('query_results', {}), ('candidate_identities', []), ('web_blocks', {}), ('web_read_failures', []), ('read_ids', []), ('executed_queries', [])]}
        facets = result.get('evidence_facets', [])
        excluded = set(s.get('excluded_evidence_ids', []))
        facet_terms = [relevance_terms(f) for f in facets]
        relevant_new = [e for e in result.get('evidence_pool', []) if _evidence_signature(e) not in before and e['id'] not in excluded and any(relevance_score(terms, (e.get('title', '') + ' ' + e.get('text', '')).casefold()) > 0 for terms in facet_terms)]
        attempted = list(s.get('attempted_plans', []))
        signature = _plan_signature(action, s['route'].task)
        attempted.append(signature)
        old_keys = {item['id'] for item in prior.get('candidate_identities', [])}
        new_keys = {item['id'] for item in result.get('candidate_identities', [])}
        unread = max(0, len(result.get('candidate_identities', [])) - len(result.get('read_ids', [])))
        old_offsets = {(key, block['offset']) for key, blocks in prior.get('web_blocks', {}).items() for block in blocks}
        continuation = bool((new_keys - old_keys) - set(result.get('read_ids', []))) or any((key, block['offset']) not in old_offsets and block.get('next_offset') is not None for key, blocks in result.get('web_blocks', {}).items() for block in blocks)
        summaries = [{key: p.get(key) for key in ('title', 'doi', 'url', 'original_url', 'source_level')} for p in result.get('candidates', [])[:20]]
        return {'effective_source': effective, 'directions': directions, 'pools': pools, 'attempted_plans': attempted,
                'delegations': s.get('delegations', 0) + 1, 'review_failed': result.get('review_failed', False), 'recursion_exhausted': result.get('recursion_exhausted', False), 'review_result': None, 'feedback_pending': False,
                'evidence': result.get('evidence', []), 'evidence_pool': result.get('evidence_pool', []),
                'evidence_facets': result.get('evidence_facets', []), 'warnings': result.get('warnings', []), 'web_search_blocked': result.get('web_search_blocked', ''), 'web_failures': result.get('web_failures', 0), 'web_backend': result.get('web_backend', 'primary'), 'web_empty_count': result.get('web_empty_count', 0), 'fallback_reason': result.get('fallback_reason', ''), 'direct_url_attempts': result.get('direct_url_attempts', []),
                'results': (s.get('results', []) + [{'source': effective, 'route': action, 'continuation_available': continuation, 'task': s['route'].task, 'directions': s['route'].directions, 'plan': result.get('plan', []), 'new_evidence': len(relevant_new), 'new_evidence_ids': [e['id'] for e in relevant_new], 'new_candidates': len(new_keys-old_keys), 'unread_candidates': unread, 'candidates': summaries, 'search_status': result.get('search_status', 'not_searched'), 'step_status': result.get('recent_result', ''), 'tool_calls': result.get('tool_calls', 0)}])[-3:]}

    def review(s):
        if on_stage: on_stage('assessing')
        try:
            result = _model(s, 'literature/review', {'question': research['query'], 'directions': s.get('directions', []), 'evidence': _summary_evidence(s), 'results': s.get('results', []), 'allowed_routes': _allowed_routes(research['source']), 'attempted_plans': s.get('attempted_plans', []), 'search_blocked': s.get('web_search_blocked', ''), 'can_read_direct_url': _can_read_direct('web', {**s, 'auto_web': research['source'] == 'auto'}, len(s.get('pools', {}).get('web_research', {}).get('candidates', []))), 'search_backend': s.get('web_backend', 'primary'), 'fallback_reason': s.get('fallback_reason', ''), 'empty_searches': s.get('web_empty_count', 0), 'attempted_direct_urls': s.get('direct_url_attempts', []), 'current_task': s['results'][-1]['task'], 'current_source': s['results'][-1]['source'], 'prior_subtask_notes': s.get('subtask_notes', [])[-10:]}, Review)
        except StructuredOutputError:
            return {'review_failed': True}
        except ModelServiceError:
            if not (s.get('evidence') or s.get('evidence_pool')): raise
            return {'review_failed': True}
        allowed_ids = {item['id'] for item in s.get('evidence', [])}
        excluded = set(s.get('excluded_evidence_ids', [])) | (set(result.irrelevant_evidence_ids) & allowed_ids)
        working = [item for item in s.get('evidence', []) if item['id'] not in excluded]
        if result.gaps or not working:
            result = result.model_copy(update={'sufficient': False})
        by_id = {item['id']: item for item in working}
        findings = [item.model_dump() for item in result.subtask_findings if all(c in by_id for c in item.citations)]
        latest = s['results'][-1]
        result = result.model_copy(update={'subtask_findings': [SubtaskFinding.model_validate(item) for item in findings]})
        note_ids = set(latest.get('new_evidence_ids', [])) | {citation for item in findings for citation in item['citations']}
        sources = [{key: item.get(key) for key in ('id', 'title', 'original_url', 'source_level')} for item in working if item['id'] in note_ids]
        note = {'index': s['delegations'], 'source': latest['source'], 'task': latest['task'], 'findings': findings,
                'sources': sources, 'gap': result.subtask_gap, 'status': 'findings' if findings else ('sources_only' if sources else 'no_evidence')}
        if on_subtask: on_subtask(note)
        return {'review_result': result, 'evidence': working, 'excluded_evidence_ids': list(excluded), 'subtask_notes': (s.get('subtask_notes', []) + [note])[-10:]}

    def answer(s):
        if on_stage: on_stage('answering')
        return {'content': '模型回答（未使用文献证据）：\n\n' + s['route'].answer}

    report_graph = build_report_graph(model=_model, on_stage=on_stage)

    def make_report(s):
        result = report_graph.invoke(s)
        gaps = result.get('research_gaps', [])
        permitted = _allowed_routes(research['source'])
        if not _web_actionable(s): permitted = [action for action in permitted if action != 'web_research']
        attempted = {tuple(item) for item in s.get('attempted_plans', [])}
        if gaps and not s.get('verification_attempted') and s.get('delegations', 0) < MAX_DELEGATIONS and s.get('decisions', 0) < MAX_DECISIONS:
            for gap in gaps:
                goal = (gap.get('query') or '').strip()
                if not goal: continue
                recommended = gap.get('route')
                choices = [recommended] if recommended in permitted else (permitted if recommended is None else [])
                choices = [action for action in choices if _plan_signature(action, goal) not in attempted]
                if choices:
                    retained = {'content': result['content'], 'evidence': result['evidence']} if result.get('evidence') and any(position[0] >= 0 for position in result.get('accepted', [])) else s.get('retained_verified', {})
                    return {'verification_gaps': gaps, 'verification_attempted': True, 'feedback_pending': True, 'feedback_actions': choices, 'feedback_goal': goal, 'retained_verified': retained}
        if not any(position[0] >= 0 for position in result.get('accepted', [])) and s.get('retained_verified'):
            retained = _retained_partial(s)
            if retained: return {**retained, 'feedback_pending': False}
        return {'content': result['content'], 'evidence': result['evidence'], 'feedback_pending': False}


    def _retained_partial(s):
        retained = s.get('retained_verified') or {}
        valid_ids = {item['id'] for item in s.get('evidence', []) + s.get('evidence_pool', [])} - set(s.get('excluded_evidence_ids', []))
        if not retained.get('evidence') or any(item['id'] not in valid_ids for item in retained['evidence']): return None
        content = retained['content'].replace('# 研究报告', '# 阶段性研究结果', 1)
        return {'content': content + '\n\n补充研究未完成，以上已核验内容仍需结合未解决缺口使用。', 'evidence': retained['evidence']}

    def partial(s):
        retained = _retained_partial(s)
        if retained: return retained
        web_pool = s.get('pools', {}).get('web_research', {})
        if (research['source'] == 'web' and s.get('web_failures', 0) and not s.get('web_search_blocked')
                and not web_pool.get('query_results') and not web_pool.get('candidates') and not s.get('evidence_pool')):
            raise RuntimeError('网页检索失败，无法获取证据，请检查搜索设置或稍后重试。')
        return render_partial(s, s.get('forced_reason', '证据不足'))

    graph = StateGraph(State)
    for name, fn in [('partial', partial), ('supervisor', supervisor), ('delegate', delegate), ('review', review), ('answer', answer), ('report', make_report)]: graph.add_node(name, fn)
    graph.add_edge(START, 'supervisor')
    graph.add_conditional_edges('supervisor', lambda s: {'direct': 'answer', 'paper_research': 'delegate', 'web_research': 'delegate', 'review': 'review', 'report': 'report', 'partial': 'partial'}[s['route'].action])
    graph.add_edge('delegate', 'supervisor')
    graph.add_edge('review', 'supervisor')
    graph.add_edge('answer', END)
    graph.add_edge('partial', END)
    graph.add_conditional_edges('report', lambda s: 'supervisor' if s.get('feedback_pending') else END)
    result = _stream_bounded(graph.compile(), {'research': research, 'history': [] if research['source'] == 'collection' else list(history),
        'candidates': [], 'query_results': {}, 'evidence': [], 'evidence_pool': [], 'evidence_facets': [research['query']], 'warnings': []})
    if result.get('recursion_exhausted'):
        result = _retained_partial(result) or render_partial(result, '研究流程达到运行上限')
    return result['content'], result.get('evidence', [])
