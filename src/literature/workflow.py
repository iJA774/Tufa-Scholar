"""Evidence-based chat graph; external text is always passed as data."""
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit
from typing import TypedDict
from pydantic import BaseModel, Field
from pypdf import PdfReader
from langgraph.graph import StateGraph, START, END
from src import repository
from src.integrations.model import structured
from src.integrations.web_search import search_web
from src.literature.retrieval import retrieve


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
    task: dict
    history: list
    direct: bool
    queries: list
    papers: list
    evidence: list
    round: int
    previous_count: int
    draft: Draft
    content: str
    warnings: list


def _stage(state, stage):
    repository.update_stage(state['task']['id'], stage)


def _model(state, prompt_id, payload, schema):
    task = state['task']
    if task.get('model_name') or task.get('reasoning_effort'):
        return structured(prompt_id, payload, schema, model_options=task)
    return structured(prompt_id, payload, schema)


def plan(s):
    _stage(s, 'planning')
    collection = s['task']['source'] == 'collection'
    history = [] if collection else repository.task_history(s['task']['id'])
    p = _model(s, 'literature/plan_review', {'question': s['task']['query'], 'history': history, 'source': s['task']['source']}, Plan)
    queries = p.queries
    if s['task']['source'] == 'web':
        queries = [q.strip() for q in queries if q.strip()]
    return {'history': history, 'direct': p.direct and not collection and s['task']['source'] != 'web', 'queries': queries or [s['task']['query']], 'papers': [], 'evidence': [], 'round': 0, 'warnings': []}


def direct(s):
    _stage(s, 'answering')
    a = _model(s, 'literature/direct', {'question': s['task']['query'], 'history': s['history']}, Answer)
    return {'content': '模型回答（未使用文献证据）：\n\n' + a.answer}


def search(s):
    _stage(s, 'searching')
    papers = list(s['papers'])
    warnings = list(s.get('warnings', []))
    if s['task']['source'] == 'collection':
        allowed = set(s['task'].get('paper_ids') or [])
        candidates = repository.project_evidence(s['task']['project_id'], list(allowed)) if allowed else []
        candidates = [p for p in candidates if p['id'] in allowed]
        ranked = repository.local_search(s['task']['project_id'], ' '.join(s['queries']), list(allowed)) if allowed else []
        order = {p['id']: i for i, p in enumerate(ranked) if p['id'] in allowed}
        candidates.sort(key=lambda p: order.get(p['id'], len(order)))
        return {'papers': candidates[:6], 'previous_count': len(s['evidence'])}
    if s['task']['source'] == 'web':
        succeeded = False
        for query in s['queries']:
            try:
                results = search_web(query, limit=5)
                succeeded = True
            except RuntimeError:
                warnings.append('web_search_failed')
                continue
            for item in results:
                parts = urlsplit(item['url'])
                url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))
                if item.get('content', '').strip() and not any(p['_dedup_url'] == url for p in papers):
                    papers.append({**item, '_dedup_url': url})
        if not succeeded and not papers:
            raise RuntimeError('网页检索失败，无法获取证据，请检查搜索设置或稍后重试。')
        return {'papers': papers[:6], 'previous_count': len(s['evidence']), 'warnings': warnings}
    for query in s['queries']:
        result = retrieve(s['task']['project_id'], query, s['task']['source'])
        warnings.extend(result.get('warnings', []))
        for paper in result['results']:
            key = paper.get('id') or paper.get('doi') or paper.get('openalex_id') or paper.get('title')
            if not any((p.get('id') or p.get('doi') or p.get('openalex_id') or p.get('title')) == key for p in papers):
                papers.append(paper)
    return {'papers': papers[:6], 'previous_count': len(s['evidence']), 'warnings': warnings}


def evidence(s):
    _stage(s, 'evidence')
    if s['task']['source'] == 'web':
        found = []
        for item in s['papers']:
            text = item.get('content', '').strip()[:1200]
            if not text:
                continue
            entry = {
                'id': 'w' + hashlib.sha256(item['_dedup_url'].encode('utf-8')).hexdigest()[:16],
                'title': item['title'], 'original_url': item['url'],
                'source_level': item['source_level'], 'provider': item['provider'],
                'retrieved_at': item['retrieved_at'], 'text': text,
            }
            if item.get('published_at'):
                entry['published_at'] = item['published_at']
            found.append(entry)
        return {'evidence': found}
    allowed = s['task'].get('paper_ids') or [] if s['task']['source'] == 'collection' else None
    local = {p['id']: p for p in repository.project_evidence(s['task']['project_id'], allowed)} if allowed is not None else {p['id']: p for p in repository.project_evidence(s['task']['project_id'])}
    found = []
    for p in s['papers']:
        if allowed is not None and (p.get('id') not in allowed or p.get('id') not in local):
            continue
        record = local.get(p.get('id'))
        if record:
            for v in record['versions']:
                if v['text_status'] != 'text_available':
                    continue
                reader = PdfReader(v['path'])
                terms = [t.lower() for t in re.findall(r'\w+', s['task']['query']) if len(t) > 1]
                pages = []
                for page_num, page in enumerate(reader.pages[:20], 1):
                    text = (page.extract_text() or '').strip()
                    if text:
                        positions = [text.lower().find(term) for term in terms if term in text.lower()]
                        start = max(0, min(positions) - 200) if positions else 0
                        pages.append((len(positions), page_num, text[start:start + 1200]))
                for _, page_num, text in sorted(pages, key=lambda x: (-x[0], x[1]))[:3]:
                    found.append({'id': f"p{p['id']}v{v['id']}p{page_num}", 'paper_id': p['id'], 'title': p['title'], 'source_level': 'full_text', 'version_id': v['id'], 'page': page_num, 'text': text})
                break
        if not any(e['title'] == p['title'] for e in found) and p.get('abstract'):
            found.append({'id': f"a{len(found)+1}", 'paper_id': p.get('id'), 'title': p['title'], 'source_level': 'abstract', 'version_id': None, 'page': None, 'text': p['abstract'][:1200], 'original_url': p.get('original_url')})
    return {'evidence': found}


def assess(s):
    _stage(s, 'assessing')
    if s['round'] >= 2 or (s['round'] and len(s['evidence']) <= s['previous_count']):
        return {'queries': []}
    g = _model(s, 'literature/assess_gap', {'question': s['task']['query'], 'evidence': s['evidence']}, Gap)
    query = g.query.strip() if s['task']['source'] == 'web' else g.query
    return {'queries': [query] if query else [], 'round': s['round'] + 1}


def synthesize(s):
    _stage(s, 'synthesizing')
    d = _model(s, 'literature/synthesize', {'question': s['task']['query'], 'history': s['history'], 'evidence': s['evidence']}, Draft)
    return {'draft': d}


def verify(s):
    _stage(s, 'verifying')
    allowed = set(s['task'].get('paper_ids') or []) if s['task']['source'] == 'collection' else None
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
    web = s['task']['source'] == 'web'
    if s.get('warnings'):
        lines.append('网页检索部分或全部失败，结果不完整，请稍后重试。' if web else '外部检索不可用，本次仅使用可获得的本地结果。')
    if web:
        if not s['evidence'] and not s.get('warnings'):
            lines.append('本次网页检索未找到可用文本证据。')
        lines.append('网页结果仅使用每条最多 1200 字符的片段；搜索摘要、网页文本与论文摘要的来源层级不同，不能视为论文全文。')
    else:
        lines.append('正文仅检查每篇首个可提取版本前 20 页中按问题词挑选的最多 3 页、每页最多 1200 字符；摘要不等同于全文证据。')
    kept = [e for e in s['evidence'] if e['id'] in used and (allowed is None or e.get('paper_id') in allowed)]
    if kept:
        def reference(e):
            if web:
                level = {'web_snippet': '搜索摘要', 'web_content': '网页文本', 'abstract': '论文摘要'}.get(e['source_level'], e['source_level'])
                return f"[{e['id']}] {e['title']} · {level} · {e['original_url']} · {e['provider']} · 获取于 {e['retrieved_at']}"
            return f"[{e['id']}] {e['title']} · " + (f"原文版本 {e['version_id']}，第 {e['page']} 页" if e.get('version_id') else '仅摘要')
        lines.append('参考证据：\n' + '\n'.join(reference(e) for e in kept))
    return {'content': '\n\n'.join(lines), 'evidence': kept}


def run(task):
    if task['source'] == 'collection' and not task.get('paper_ids'):
        return '证据不足：所选收藏夹没有文献。', []
    graph = StateGraph(State)
    for name, fn in [('plan', plan), ('direct', direct), ('search', search), ('evidence', evidence), ('assess', assess), ('synthesize', synthesize), ('verify', verify)]:
        graph.add_node(name, fn)
    graph.add_edge(START, 'plan')
    graph.add_conditional_edges('plan', lambda s: 'direct' if s['direct'] else 'search')
    graph.add_edge('direct', END)
    graph.add_edge('search', 'evidence')
    graph.add_edge('evidence', 'assess')
    graph.add_conditional_edges('assess', lambda s: 'search' if s['queries'] else 'synthesize')
    graph.add_edge('synthesize', 'verify')
    graph.add_edge('verify', END)
    result = graph.compile().invoke({'task': task})
    return result['content'], result.get('evidence', [])
