"""Evidence-based chat graph; external text is always passed as data."""
import re
import logging
from urllib.parse import urlsplit, urlunsplit
from typing import TypedDict, Literal
from pydantic import model_validator
from src.literature.retrieval import _candidate_ids
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from src.integrations.model import structured
from src.infrastructure.diagnostics import record_failure
from src.tools import ToolContext, ToolRegistry
from src.literature.retrieval import rank_candidates
from src.literature.keywords import query_terms

logger = logging.getLogger('tufa.agent')
registry = ToolRegistry()


def _context(research):
    return ToolContext(research["project_id"], research["source"], tuple(research.get("paper_ids") or ()))


class Claim(BaseModel):
    text: str
    citations: list[str]


class Draft(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    limitations: str = ''


class Verdict(BaseModel):
    supported: bool


class State(TypedDict, total=False):
    research: dict
    effective_source: str
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
    delegations: int
    no_progress: bool
    review_result: object
    review_pending: bool
    results: list
    route: object
    decisions: int
    forced: bool
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


def search(s):
    research = s['research']
    query_results = {key: list(items) for key, items in s.get('query_results', {}).items()}
    warnings = list(s.get('warnings', []))
    queries = [' '.join(s['queries'])] if research['source'] == 'collection' else s['queries']
    succeeded = False
    for query in queries:
        key = query.strip().casefold()
        try:
            if research['source'] == 'web':
                results = registry.invoke('search_web', {'query': query, 'limit': 5}, context=_context(research))
                results = [item for item in results if item.get('content', '').strip()]
            else:
                result = registry.invoke('search_papers', {'query': query}, context=_context(research))
                warnings.extend(result.get('warnings', []))
                results = result['results']
            succeeded = True
        except RuntimeError:
            if research['source'] != 'web':
                raise
            warnings.append('web_search_failed')
            continue
        entries = query_results.setdefault(key, [])
        for position, paper in enumerate(results, 1):
            rank = paper.get('rank')
            if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
                rank = position
            entries.append({'paper': paper, 'rank': rank})
    candidates = rank_candidates(query_results, research['source'])
    if research['source'] == 'web' and not succeeded and not candidates:
        raise RuntimeError('网页检索失败，无法获取证据，请检查搜索设置或稍后重试。')
    return {'candidates': candidates, 'papers': candidates[:6], 'query_results': query_results, 'warnings': warnings}


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
    terms = [query_terms(facet) for facet in facets]
    texts = [(entry.get('title', '') + ' ' + entry.get('text', '')).casefold() for entry in pool]
    scores = [[sum(term in text for term in facet) for text in texts] for facet in terms]
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
        add(index)
    return [pool[index] for index in selected]


def evidence(s):
    query = s.get('evidence_query') or s['research']['query']
    found = registry.invoke(
        'collect_evidence', {'query': query, 'papers': s['papers']}, context=_context(s['research'])
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
    return {'evidence': _select_evidence(pool, facets), 'evidence_pool': pool,
            'evidence_facets': facets, 'new_evidence': new_evidence}


def synthesize(s):
    d = _model(s, 'literature/synthesize', {'question': s['research']['query'], 'history': s['history'], 'evidence': s['evidence'], 'review': s['review_result'].model_dump() if s.get('review_result') else None}, Draft)
    return {'draft': d}


def verify(s):
    allowed = set(s['research'].get('paper_ids') or []) if s['research']['source'] == 'collection' else None
    by_id = {e['id']: e for e in s['evidence'] if allowed is None or e.get('paper_id') in allowed}
    lines = []
    used = set()
    rejected = False
    rejection_feedback = []
    for c in s['draft'].claims:
        cited = [by_id[i] for i in c.citations if i in by_id]
        numbers = re.findall(r'\d+(?:\.\d+)?%?', c.text)
        source_numbers = {n for e in cited for n in re.findall(r'\d+(?:\.\d+)?%?', e['text'])}
        if not cited or len(cited) != len(c.citations) or any(n not in source_numbers for n in numbers):
            rejected = True
            rejection_feedback.append({'claim': c.text, 'reason': '引用、数值或成本依据不匹配'})
            continue
        # Accuracy evidence alone cannot establish a cost claim, even if the numbers match.
        if re.search(r'成本|费用|cost|expense', c.text, re.I) and not any(re.search(r'成本|费用|cost|expense|\$|美元', e['text'], re.I) for e in cited):
            rejected = True
            rejection_feedback.append({'claim': c.text, 'reason': '引用、数值或成本依据不匹配'})
            continue
        v = _model(s, 'literature/verify_claim', {'claim': c.text, 'evidence': cited}, Verdict)
        if v.supported:
            used.update(c.citations)
            lines.append(c.text + ' ' + ' '.join(f'[{i}]' for i in c.citations))
        else:
            rejected = True
            rejection_feedback.append({'claim': c.text, 'reason': '语义核验未通过'})
    if s['draft'].limitations:
        lines.append('局限：模型指出现有证据存在不足，需进一步核查来源。')
    if rejected or not used:
        lines.append('证据不足：部分结论无法由所引材料支持。')
    web = s['research']['source'] == 'web'
    if s.get('warnings'):
        lines.append('网页检索部分或全部失败，结果不完整，请稍后重试。' if web else '外部检索不可用，本次仅使用可获得的本地结果。')
    if web:
        if not s['evidence'] and not s.get('warnings'):
            lines.append('本次网页检索未找到可用文本证据。')
        lines.append('网页结果仅使用每条最多 1200 字符的片段；搜索摘要、网页文本与论文摘要的来源层级不同，不能视为论文全文。')
    else:
        lines.append('正文从每篇最新可提取版本的已索引页面中按问题词挑选最多 6 个页面片段，每个最多 1200 字符；摘要不等同于全文证据。')
    kept = [e for e in s['evidence'] if e['id'] in used and (allowed is None or e.get('paper_id') in allowed)]
    if kept:
        def reference(e):
            if web:
                level = {'web_snippet': '搜索摘要', 'web_content': '网页文本', 'abstract': '论文摘要'}.get(e['source_level'], e['source_level'])
                return f"[{e['id']}] {e['title']} · {level} · {e['original_url']} · {e['provider']} · 获取于 {e['retrieved_at']}"
            return f"[{e['id']}] {e['title']} · " + (f"原文版本 {e['version_id']}，第 {e['page']} 页" if e.get('version_id') else '仅摘要')
        lines.append('参考证据：\n' + '\n'.join(reference(e) for e in kept))
    return {'content': '\n\n'.join(lines), 'evidence': kept, 'rejection_feedback': rejection_feedback}



class Route(BaseModel):
    action: Literal['direct', 'paper_research', 'web_research', 'review', 'report']
    task: str = Field(default='', max_length=1000)
    answer: str = Field(default='', max_length=12000)

    @model_validator(mode='after')
    def validate_action(self):
        if self.action in ('paper_research', 'web_research') and not self.task.strip():
            raise ValueError('委派任务不能为空')
        if self.action == 'direct' and not self.answer.strip():
            raise ValueError('直接回答不能为空')
        return self


class Specialist(BaseModel):
    action: Literal['search', 'read', 'finish']
    query: str = Field(default='', max_length=1000)
    candidate_ids: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def validate_action(self):
        if self.action == 'search' and not self.query.strip():
            raise ValueError('搜索查询不能为空')
        if self.action == 'read' and not self.candidate_ids:
            raise ValueError('精读候选不能为空')
        return self


class Review(BaseModel):
    sufficient: bool
    gaps: list[str] = Field(default_factory=list, max_length=10)
    summary: str = Field(max_length=2000)

    @model_validator(mode='after')
    def validate_gaps(self):
        if any(len(gap) > 1000 for gap in self.gaps):
            raise ValueError('审查缺口过长')
        return self


def _summary_evidence(s):
    return [{**{key: e.get(key) for key in ('id', 'title', 'source_level', 'paper_id', 'version_id', 'page', 'original_url', 'provider', 'retrieved_at')}, 'text': e.get('text', '')[:600]} for e in s.get('evidence', [])]


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


def _research_graph(kind, on_stage):
    def decide(s):
        views, identities = _candidate_views(s)
        allowed = {'search_web' if kind == 'web' else 'search_papers', 'collect_evidence'}
        tools = [tool for tool in registry.list_tools(_context(s['research'])) if tool['name'] in allowed]
        payload = {
            'task': s['task'], 'role': kind, 'source': s['research']['source'], 'tools': tools,
            'candidates': [{'id': key, 'title': p.get('title', ''), 'summary': (p.get('abstract') or p.get('content') or '')[:500]} for key, p in views[:20]],
            'candidate_count': len(views), 'recent_result': s.get('recent_result', ''),
            'evidence': _summary_evidence(s), 'calls_remaining': 6 - s.get('tool_calls', 0),
        }
        valid_ids = {key for key, _ in views}
        for attempt in range(2):
            choice = _model(s, 'literature/research', payload, Specialist)
            logger.info('prompt_id=literature/research action=%s tool_calls=%d attempt=%d', choice.action, s.get('tool_calls', 0), attempt + 1)
            if choice.action != 'read' or (len(set(choice.candidate_ids)) == len(choice.candidate_ids) and all(i in valid_ids for i in choice.candidate_ids)):
                break
            diagnostic_id = record_failure(kind='semantic_candidates', payload=payload, schema=Specialist.model_json_schema(),
                response=choice.model_dump(), errors=['invalid_candidate_ids'],
                context={'candidate_ids': sorted(valid_ids), 'prompt_id': 'literature/research', 'attempt': attempt + 1})
            logger.warning('prompt_id=literature/research diagnostic_id=%s', diagnostic_id)
            if attempt:
                raise ValueError('精读候选标识无效')
            payload = {**payload, 'validation_feedback': {'code': 'invalid_candidate_ids', 'allowed_ids': [key for key, _ in views[:20]]}}
        return {'choice': choice, 'candidate_identities': identities}

    def invoke(s):
        choice = s['choice']
        if choice.action == 'search':
            if on_stage: on_stage('searching_web' if kind == 'web' else 'searching')
            update = search({**s, 'queries': [choice.query]})
            result = f"检索到 {len(update['candidates'])} 个累计候选"
        else:
            if on_stage: on_stage('evidence')
            mapping = dict(_candidate_views(s)[0])
            if len(set(choice.candidate_ids)) != len(choice.candidate_ids) or any(i not in mapping for i in choice.candidate_ids):
                raise ValueError('精读候选标识无效')
            update = evidence({**s, 'papers': [mapping[i] for i in choice.candidate_ids], 'evidence_query': s['task']})
            result = f"本次新增证据：{update['new_evidence']}"
        return {**update, 'tool_calls': s.get('tool_calls', 0) + 1, 'recent_result': result}

    graph = StateGraph(State)
    graph.add_node('decide', decide)
    graph.add_node('invoke', invoke)
    graph.add_edge(START, 'decide')
    graph.add_conditional_edges('decide', lambda s: END if s['choice'].action == 'finish' or s.get('tool_calls', 0) >= 6 else 'invoke')
    graph.add_conditional_edges('invoke', lambda s: END if s['tool_calls'] >= 6 else 'decide')
    return graph.compile()


def run(research: dict, *, history: list, on_stage=None):
    if research['source'] == 'collection' and not research.get('paper_ids'):
        return '证据不足：所选收藏夹没有文献。', []
    paper_graph = _research_graph('paper', on_stage)
    web_graph = _research_graph('web', on_stage)

    def supervisor(s):
        if on_stage: on_stage('planning')
        count = s.get('delegations', 0)
        reviewed = s.get('review_result')
        exhausted = count >= 3 or s.get('no_progress', False)
        effective = s.get('effective_source')
        if exhausted:
            actions = []
        elif research['source'] == 'auto' and not effective:
            actions = ['paper_research', 'web_research']
        else:
            actions = ['web_research'] if (effective or research['source']) == 'web' else ['paper_research']
        if count and not s.get('review_pending', False): actions.append('review')
        if count and reviewed: actions.append('report')
        if not count and research['source'] not in ('collection', 'web'): actions.append('direct')
        if exhausted or s.get('decisions', 0) >= 12 or not actions:
            reason = ('补研没有新增证据' if s.get('no_progress') else
                      '研究委派预算已耗尽' if count >= 3 else '主控决策预算已耗尽')
            action = 'report' if reviewed else 'review'
            logger.info('prompt_id=runtime/route action=%s forced=true delegations=%d decisions=%d', action, count, s.get('decisions', 0))
            return {'route': Route(action=action), 'forced': True, 'forced_reason': reason}
        payload = {'question': research['query'], 'history': s['history'], 'source': research['source'],
            'available_actions': actions, 'effective_source': effective, 'results': s.get('results', [])[-3:], 'evidence': _summary_evidence(s),
            'review': reviewed.model_dump() if reviewed else None, 'delegations_remaining': max(0, 3-count),
            'decisions_remaining': max(0, 12-s.get('decisions', 0)), 'no_progress': s.get('no_progress', False)}
        for attempt in range(2):
            route = _model(s, 'runtime/route', payload, Route)
            logger.info('prompt_id=runtime/route action=%s allowed=%s delegations=%d decisions=%d attempt=%d', route.action, actions, count, s.get('decisions', 0), attempt + 1)
            if route.action in actions:
                break
            diagnostic_id = record_failure(kind='semantic_route', payload=payload, schema=Route.model_json_schema(),
                response=route.model_dump(), errors=['invalid_action'],
                context={'allowed_actions': actions, 'prompt_id': 'runtime/route', 'attempt': attempt + 1})
            logger.warning('prompt_id=runtime/route diagnostic_id=%s', diagnostic_id)
            if attempt:
                raise ValueError('主控选择了当前范围或状态不允许的操作')
            payload = {**payload, 'validation_feedback': {'code': 'invalid_action', 'allowed_actions': actions}}
        return {'route': route, 'decisions': s.get('decisions', 0) + 1}

    def delegate(s):
        before = {_evidence_signature(e) for e in s.get('evidence_pool', [])}
        effective = s.get('effective_source') or (('web' if s['route'].action == 'web_research' else 'all') if research['source'] == 'auto' else research['source'])
        scoped_research = {**research, 'source': effective}
        result = (web_graph if s['route'].action == 'web_research' else paper_graph).invoke({**s, 'research': scoped_research, 'task': s['route'].task, 'tool_calls': 0, 'recent_result': ''})
        result.pop('research', None)
        after = {_evidence_signature(e) for e in result.get('evidence_pool', [])}
        count = s.get('delegations', 0) + 1
        return {**result, 'effective_source': effective, 'delegations': count, 'no_progress': count > 1 and not (after - before),
                'review_pending': False, 'review_result': None, 'results': (s.get('results', []) + [f"{s['route'].task}: 新增证据 {len(after-before)} 条；工具调用 {result.get('tool_calls', 0)}/6"])[-3:]}

    def review(s):
        if on_stage: on_stage('assessing')
        result = _model(s, 'literature/review', {'question': research['query'], 'evidence': _summary_evidence(s), 'results': s.get('results', [])}, Review)
        return {'review_result': result, 'review_pending': True}

    def answer(s):
        if on_stage: on_stage('answering')
        return {'content': '模型回答（未使用文献证据）：\n\n' + s['route'].answer}

    def draft(s):
        if on_stage: on_stage('synthesizing')
        return synthesize(s)

    def check(s):
        if on_stage: on_stage('verifying')
        result = verify(s)
        return {'verified_content': result['content'], 'verified_evidence': result['evidence'], 'rejected': bool(result['rejection_feedback']), 'rejection_feedback': result['rejection_feedback']}

    def revise(s):
        if on_stage: on_stage('synthesizing')
        d = _model(s, 'literature/revise', {'question': research['query'], 'draft': s['draft'].model_dump(), 'feedback': s['rejection_feedback'], 'evidence': s['evidence'], 'review': s['review_result'].model_dump() if s.get('review_result') else None}, Draft)
        return {'draft': d, 'revised': True}

    report = StateGraph(State)
    for name, fn in [('synthesize', draft), ('verify', check), ('revise', revise)]: report.add_node(name, fn)
    report.add_edge(START, 'synthesize')
    report.add_edge('synthesize', 'verify')
    report.add_conditional_edges('verify', lambda s: 'revise' if s['rejected'] and not s.get('revised') else END)
    report.add_edge('revise', 'verify')
    report_graph = report.compile()

    def make_report(s):
        result = report_graph.invoke({**s, 'research': {**research, 'source': s.get('effective_source') or research['source']}})
        content = result['verified_content']
        review_result = s.get('review_result')
        if s.get('forced') or (review_result and not review_result.sufficient):
            gaps = '；'.join(review_result.gaps) if review_result else ''
            content += '\n\n证据不足：' + (s.get('forced_reason', '') + '；' if s.get('forced_reason') else '') + (gaps or '仍需进一步核查。')
        return {'content': content, 'evidence': result['verified_evidence']}

    graph = StateGraph(State)
    for name, fn in [('supervisor', supervisor), ('delegate', delegate), ('review', review), ('answer', answer), ('report', make_report)]: graph.add_node(name, fn)
    graph.add_edge(START, 'supervisor')
    graph.add_conditional_edges('supervisor', lambda s: {'direct': 'answer', 'paper_research': 'delegate', 'web_research': 'delegate', 'review': 'review', 'report': 'report'}[s['route'].action])
    graph.add_edge('delegate', 'supervisor')
    graph.add_edge('review', 'supervisor')
    graph.add_edge('answer', END)
    graph.add_edge('report', END)
    result = graph.compile().invoke({'research': research, 'history': [] if research['source'] == 'collection' else list(history),
        'candidates': [], 'query_results': {}, 'evidence': [], 'evidence_pool': [], 'evidence_facets': [research['query']], 'warnings': []}, {'recursion_limit': 100})
    return result['content'], result.get('evidence', [])
