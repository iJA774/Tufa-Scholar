"""Evidence-based chat graph; external text is always passed as data."""
import re
from urllib.parse import urlsplit, urlunsplit
from typing import TypedDict
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from src.integrations.model import structured
from src.tools import ToolContext, ToolRegistry
from src.literature.retrieval import rank_candidates
from src.literature.keywords import query_terms

registry = ToolRegistry()


def _context(research):
    return ToolContext(research["project_id"], research["source"], tuple(research.get("paper_ids") or ()))


class Plan(BaseModel):
    direct: bool = False
    queries: list[str] = Field(default_factory=list, max_length=3)


class Gap(BaseModel):
    query: str = ''


class Claim(BaseModel):
    text: str
    citations: list[str]


class Draft(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    limitations: str = ''


class Verdict(BaseModel):
    supported: bool


class Answer(BaseModel):
    answer: str


class State(TypedDict, total=False):
    research: dict
    history: list
    direct: bool
    queries: list
    evidence_query: str
    candidates: list
    papers: list
    query_results: dict
    new_evidence: bool
    evidence: list
    evidence_pool: list
    evidence_facets: list
    round: int
    draft: Draft
    content: str
    warnings: list


def _model(state, prompt_id, payload, schema):
    research = state['research']
    if research.get('model_name') or research.get('reasoning_effort'):
        return structured(prompt_id, payload, schema, model_options=research)
    return structured(prompt_id, payload, schema)


def plan(s):
    collection = s['research']['source'] == 'collection'
    history = [] if collection else s['history']
    p = _model(s, 'literature/plan_review', {'question': s['research']['query'], 'history': history, 'source': s['research']['source']}, Plan)
    queries = p.queries
    if s['research']['source'] == 'web':
        queries = [q.strip() for q in queries if q.strip()]
    return {
        'history': history, 'direct': p.direct and not collection and s['research']['source'] != 'web',
        'queries': queries or [s['research']['query']], 'evidence_query': s['research']['query'],
        'candidates': [], 'papers': [], 'query_results': {}, 'new_evidence': False,
        'evidence': [], 'evidence_pool': [], 'evidence_facets': [s['research']['query']],
        'round': 0, 'warnings': [],
    }


def direct(s):
    a = _model(s, 'literature/direct', {'question': s['research']['query'], 'history': s['history']}, Answer)
    return {'content': '模型回答（未使用文献证据）：\n\n' + a.answer}


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


def assess(s):
    if s['round'] >= 2 or (s['round'] and not s['new_evidence']):
        return {'queries': []}
    g = _model(s, 'literature/assess_gap', {'question': s['research']['query'], 'evidence': s['evidence']}, Gap)
    query = g.query.strip() if s['research']['source'] == 'web' else g.query
    return {'queries': [query] if query else [], 'evidence_query': query, 'round': s['round'] + 1}


def synthesize(s):
    d = _model(s, 'literature/synthesize', {'question': s['research']['query'], 'history': s['history'], 'evidence': s['evidence']}, Draft)
    return {'draft': d}


def verify(s):
    allowed = set(s['research'].get('paper_ids') or []) if s['research']['source'] == 'collection' else None
    by_id = {e['id']: e for e in s['evidence'] if allowed is None or e.get('paper_id') in allowed}
    lines = []
    used = set()
    rejected = False
    for c in s['draft'].claims:
        cited = [by_id[i] for i in c.citations if i in by_id]
        numbers = re.findall(r'\d+(?:\.\d+)?%?', c.text)
        source_numbers = {n for e in cited for n in re.findall(r'\d+(?:\.\d+)?%?', e['text'])}
        if not cited or len(cited) != len(c.citations) or any(n not in source_numbers for n in numbers):
            rejected = True
            continue
        # Accuracy evidence alone cannot establish a cost claim, even if the numbers match.
        if re.search(r'成本|费用|cost|expense', c.text, re.I) and not any(re.search(r'成本|费用|cost|expense|\$|美元', e['text'], re.I) for e in cited):
            rejected = True
            continue
        v = _model(s, 'literature/verify_claim', {'claim': c.text, 'evidence': cited}, Verdict)
        if v.supported:
            used.update(c.citations)
            lines.append(c.text + ' ' + ' '.join(f'[{i}]' for i in c.citations))
        else:
            rejected = True
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
    return {'content': '\n\n'.join(lines), 'evidence': kept}


def run(research: dict, *, history: list, on_stage=None):
    if research['source'] == 'collection' and not research.get('paper_ids'):
        return '证据不足：所选收藏夹没有文献。', []
    graph = StateGraph(State)
    for name, fn, stage in [('plan', plan, 'planning'), ('direct', direct, 'answering'), ('search', search, 'searching'), ('evidence', evidence, 'evidence'), ('assess', assess, 'assessing'), ('synthesize', synthesize, 'synthesizing'), ('verify', verify, 'verifying')]:
        def wrapped(state, fn=fn, stage=stage):
            if on_stage is not None:
                on_stage(stage)
            return fn(state)
        graph.add_node(name, wrapped)
    graph.add_edge(START, 'plan')
    graph.add_conditional_edges('plan', lambda s: 'direct' if s['direct'] else 'search')
    graph.add_edge('direct', END)
    graph.add_edge('search', 'evidence')
    graph.add_edge('evidence', 'assess')
    graph.add_conditional_edges('assess', lambda s: 'search' if s['queries'] else 'synthesize')
    graph.add_edge('synthesize', 'verify')
    graph.add_edge('verify', END)
    result = graph.compile().invoke({'research': research, 'history': [] if research['source'] == 'collection' else list(history)})
    return result['content'], result.get('evidence', [])
