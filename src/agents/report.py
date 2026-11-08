"""Bounded, evidence-verified research report graph."""
import re
from typing import TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from langgraph.graph import StateGraph, START, END


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1500)
    citations: list[str] = Field(min_length=1, max_length=12)


class Section(BaseModel):
    model_config = ConfigDict(extra='forbid')
    heading: str = Field(min_length=1, max_length=150)
    paragraphs: list[Claim] = Field(min_length=1, max_length=3)


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sections: list[Section] = Field(max_length=6)

    @model_validator(mode='after')
    def bounded_paragraphs(self):
        if sum(len(section.paragraphs) for section in self.sections) > 12:
            raise ValueError('报告段落数量超限')
        return self


class Verdict(BaseModel):
    supported: bool


class ReportState(TypedDict, total=False):
    research: dict
    history: list
    evidence: list
    review_result: object
    forced_reason: str
    directions: list
    warnings: list
    draft: Draft
    accepted: list
    rejection_feedback: list
    revised: bool
    content: str


_UNSAFE = re.compile(r'```|<[^>]+>|https?://|\[[^\]\n]+\]\s*\([^)]*\)|\[(?:[a-zA-Z]+[\w-]*\d+[\w-]*|\d+)\]', re.I)
_COST = re.compile(r'成本|费用|cost|expense', re.I)
_COST_EVIDENCE = re.compile(r'成本|费用|cost|expense|\$|美元', re.I)
_NUMBER = re.compile(r'\d+(?:\.\d+)?%?')


def verify(s, model):
    allowed = set(s['research'].get('paper_ids') or []) if s['research']['source'] == 'collection' else None
    by_id = {e['id']: e for e in s['evidence'] if allowed is None or e.get('paper_id') in allowed}
    accepted, feedback = [], []
    for si, section in enumerate(s['draft'].sections):
        for pi, paragraph in enumerate(section.paragraphs):
            cited = [by_id[i] for i in paragraph.citations if i in by_id]
            text = section.heading + '\n' + paragraph.text
            numbers = _NUMBER.findall(text)
            source_numbers = {n for e in cited for n in _NUMBER.findall(e.get('text', ''))}
            reason = ''
            if _UNSAFE.search(text): reason = '格式或引用不安全'
            elif not cited or len(cited) != len(paragraph.citations) or any(n not in source_numbers for n in numbers): reason = '引用或数值依据不匹配'
            elif _COST.search(text) and not any(_COST_EVIDENCE.search(e.get('text', '')) for e in cited): reason = '缺少成本依据'
            elif not model(s, 'literature/verify_claim', {'heading': section.heading, 'claim': paragraph.text, 'evidence': cited}, Verdict).supported: reason = '语义核验未通过'
            if reason:
                feedback.append({'section_index': si, 'paragraph_index': pi, 'reason': reason})
            else:
                accepted.append((si, pi))
    if not s['draft'].sections and s['evidence']:
        feedback.append({'section_index': None, 'paragraph_index': None, 'reason': '报告为空'})
    return {'accepted': accepted, 'rejection_feedback': feedback}


def _reference(e):
    level = e.get('source_level', '')
    if level in ('web_snippet', 'web_content'):
        label = {'web_snippet': '搜索摘要', 'web_content': '网页文本'}.get(level, level)
        return f"[{e['id']}] {e.get('title', '')} · {label} · {e.get('provider', '')} · {e.get('original_url', '')} · 获取于 {e.get('retrieved_at', '')}"
    if e.get('version_id'):
        return f"[{e['id']}] {e.get('title', '')} · 原文版本 {e['version_id']}，第 {e.get('page', '')} 页"
    return f"[{e['id']}] {e.get('title', '')} · 论文摘要" + (f" · {e['original_url']}" if e.get('original_url') else '')


def render(s):
    accepted = set(map(tuple, s.get('accepted', [])))
    lines = ['# 研究报告']
    used = set()
    for si, section in enumerate(s['draft'].sections):
        paragraphs = [p for pi, p in enumerate(section.paragraphs) if (si, pi) in accepted]
        if not paragraphs: continue
        lines.append('## ' + section.heading)
        for p in paragraphs:
            used.update(p.citations)
            lines.append(p.text + ' ' + ' '.join(f'[{i}]' for i in p.citations))
    kept = [e for e in s['evidence'] if e['id'] in used]
    lines.append('## 范围与待核查')
    scope = {'auto': '自动选择来源', 'all': '项目与外部论文', 'local': '项目论文', 'openalex': '外部论文', 'collection': '指定收藏夹', 'web': '网页'}.get(s['research']['source'], '所选来源')
    lines.append('首选来源：' + scope + '。')
    levels = []
    if any(e.get('version_id') for e in kept): levels.append('论文原文页面片段')
    if any(e.get('source_level') == 'abstract' or (not e.get('version_id') and e.get('source_level') not in ('web_snippet', 'web_content')) for e in kept): levels.append('论文摘要')
    if any(e.get('source_level') == 'web_snippet' for e in kept): levels.append('网页搜索摘要')
    if any(e.get('source_level') == 'web_content' for e in kept): levels.append('网页文本片段')
    if levels:
        lines.append('实际引用的资料：' + '、'.join(levels) + '。')
    if s.get('rejection_feedback') or not kept:
        lines.append('证据不足：部分内容未通过核验或没有可用证据，需进一步核查。')
    review = s.get('review_result')
    if s.get('forced_reason') and (not review or not review.sufficient):
        lines.append('研究在预算或流程边界处结束，结论仍需进一步核查。')
    warnings = s.get('warnings', [])
    if 'web_search_failed' in warnings:
        lines.append('网页检索部分或全部失败，结果不完整，请稍后重试。')
    if any(w != 'web_search_failed' for w in warnings):
        lines.append('论文外部检索部分失败，结果可能不完整。')
    if s['research']['source'] == 'web' and not s['evidence'] and not warnings:
        lines.append('本次网页检索未找到可用文本证据。')
    gaps = getattr(review, 'gaps', []) if review else []
    if review and not review.sufficient:
        lines.append('现有证据尚不足以全面回答问题。')
    steps = getattr(review, 'next_steps', []) if review else []
    directions = [step.direction for step in steps] or gaps
    for direction in directions[:3]:
        clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', direction)
        clean = re.sub(r'https?://\S+|<[^>]*>', '', clean)
        clean = re.sub(r'[`*_#\[\]\r\n]+', ' ', clean)
        clean = ' '.join(clean.split())[:150]
        if clean:
            lines.append('待查方向：' + clean + '。')
    lines.append('材料可能包含原文片段、论文摘要或网页片段；摘要和片段不代表完整原文。')
    if kept:
        lines.append('## 参考证据\n' + '\n'.join(_reference(e) for e in kept))
    return {'content': '\n\n'.join(lines), 'evidence': kept}


def build_report_graph(model, on_stage=None):
    def draft(s):
        if on_stage: on_stage('synthesizing')
        review = s.get('review_result')
        payload = {'question': s['research']['query'], 'history': s.get('history', []), 'directions': s.get('directions', []), 'evidence': s['evidence'], 'review': review.model_dump() if review else None}
        return {'draft': model(s, 'literature/synthesize', payload, Draft)}
    def check(s):
        if on_stage: on_stage('verifying')
        return verify(s, model)
    def revise(s):
        if on_stage: on_stage('synthesizing')
        review = s.get('review_result')
        payload = {'question': s['research']['query'], 'draft': s['draft'].model_dump(), 'feedback': s['rejection_feedback'], 'directions': s.get('directions', []), 'evidence': s['evidence'], 'review': review.model_dump() if review else None}
        return {'draft': model(s, 'literature/revise', payload, Draft), 'revised': True}
    graph = StateGraph(ReportState)
    graph.add_node('draft', draft); graph.add_node('verify', check); graph.add_node('revise', revise); graph.add_node('render', render)
    graph.add_edge(START, 'draft'); graph.add_edge('draft', 'verify')
    graph.add_conditional_edges('verify', lambda s: 'revise' if s['rejection_feedback'] and not s.get('revised') else 'render')
    graph.add_edge('revise', 'verify'); graph.add_edge('render', END)
    return graph.compile()
