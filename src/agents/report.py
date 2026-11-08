"""Bounded, evidence-verified research report graph."""
import re
import logging
from decimal import Decimal
from typing import TypedDict, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from langgraph.graph import StateGraph, START, END
from src.infrastructure.diagnostics import record_failure
from src.integrations.model import StructuredOutputError

logger = logging.getLogger('tufa.agent')


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1500)
    citations: list[str] = Field(max_length=12)


class Section(BaseModel):
    model_config = ConfigDict(extra='forbid')
    heading: str = Field(min_length=1, max_length=150)
    paragraphs: list[Claim] = Field(min_length=1, max_length=3)


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sections: list[Section] = Field(max_length=6)
    limitations: list[Claim] = Field(default_factory=list, max_length=3)

    @model_validator(mode='before')
    @classmethod
    def recover_known_shape(cls, value):
        if not isinstance(value, dict): return value
        value = dict(value)
        value.pop('verification_gaps', None)
        if 'heading' in value and 'paragraphs' in value and isinstance(value.get('sections'), list):
            try:
                candidate = Section.model_validate({'heading': value['heading'], 'paragraphs': value['paragraphs']})
            except ValidationError:
                return value
            sections = list(value['sections'])
            duplicate = False
            for item in sections:
                try:
                    if Section.model_validate(item) == candidate:
                        duplicate = True
                        break
                except ValidationError:
                    pass
            if not duplicate: sections.append(candidate.model_dump())
            value['sections'] = sections
            value.pop('heading'); value.pop('paragraphs')
        return value

    @model_validator(mode='after')
    def bounded_paragraphs(self):
        if sum(len(section.paragraphs) for section in self.sections) + len(self.limitations) > 12:
            raise ValueError('报告段落数量超限')
        return self


class Verdict(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['supported', 'qualified', 'missing_evidence', 'unsupported']
    reason: str = Field(default='', max_length=1000)
    suggestion: str = Field(default='', max_length=1000)
    research_query: str = Field(default='', max_length=1000)
    research_route: Literal['paper_research', 'web_research'] | None = None

    @model_validator(mode='after')
    def validate_details(self):
        if self.status != 'supported' and not self.reason.strip(): raise ValueError('缺少原因')
        if self.status == 'qualified' and not self.suggestion.strip(): raise ValueError('缺少修订建议')
        if self.status == 'missing_evidence' and not self.research_query.strip(): raise ValueError('缺少补研查询')
        return self


class Patch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    section_index: int
    paragraph_index: int
    text: str = Field(min_length=1, max_length=1500)
    citations: list[str] = Field(min_length=1, max_length=12)


class PatchSet(BaseModel):
    model_config = ConfigDict(extra='forbid')
    patches: list[Patch] = Field(max_length=12)


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
    research_gaps: list
    verification_gaps: list
    outcomes: list
    patched_positions: list
    empty_retried: bool
    draft_format_failed: bool


_UNSAFE = re.compile(r'```|<[^>]+>|https?://|\[[^\]\n]+\]\s*\([^)]*\)|\[(?:[a-zA-Z]+[\w-]*\d+[\w-]*|\d+)\]', re.I)
_COST = re.compile(r'成本|费用|cost|expense', re.I)
_COST_EVIDENCE = re.compile(r'成本|费用|cost|expense|\$|美元', re.I)
_NUM = r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_MEASURE = re.compile(r'(?<![A-Za-z0-9_.,])(?:百分之\s*(' + _NUM + r')|(' + _NUM + r')\s*(milliseconds?|seconds?|percent|gib|mib|ms|gb|mb|kg|km|毫秒|秒|%))(?![A-Za-z0-9_])', re.I)


def _measurements(text):
    result = set()
    for prefix, number, unit in _MEASURE.findall(text):
        unit = unit.casefold()
        value = Decimal((prefix or number).replace(',', ''))
        if prefix or unit in ('%', 'percent'):
            unit = '%'
        elif unit in ('milliseconds', 'millisecond', '毫秒', 'ms'):
            value /= Decimal(1000); unit = 'second'
        elif unit in ('seconds', 'second', '秒'):
            unit = 'second'
        result.add((value, unit))
    return result


def verify(s, model):
    allowed = set(s['research'].get('paper_ids') or []) if s['research']['source'] == 'collection' else None
    by_id = {e['id']: e for e in s['evidence'] if allowed is None or e.get('paper_id') in allowed}
    previous = {(o['section_index'], o['paragraph_index']): o for o in s.get('outcomes', [])}
    outcomes = []
    units = [(si, pi, section.heading, p) for si, section in enumerate(s['draft'].sections) for pi, p in enumerate(section.paragraphs)]
    units += [(-1, pi, '局限', p) for pi, p in enumerate(s['draft'].limitations)]
    for si, pi, heading, paragraph in units:
        prior = previous.get((si, pi))
        if s.get('revised') and prior and (prior['status'] != 'qualified' or (si, pi) not in set(map(tuple, s.get('patched_positions', [])))):
            outcomes.append(prior); continue
        cited = [by_id[i] for i in paragraph.citations if i in by_id]
        text = heading + '\n' + paragraph.text
        measurements = _measurements(text)
        source_measurements = set().union(*(_measurements(e.get('text', '')) for e in cited)) if cited else set()
        reason = ''
        if _UNSAFE.search(text): reason = '格式或引用不安全'
        elif not paragraph.citations: reason = '段落缺少证据引用'
        elif len(cited) != len(paragraph.citations) or not measurements.issubset(source_measurements): reason = '引用或测量值依据不匹配'
        elif si != -1 and _COST.search(text) and not any(_COST_EVIDENCE.search(e.get('text', '')) for e in cited): reason = '缺少成本依据'
        if reason:
            verdict = Verdict(status='unsupported', reason=reason)
        else:
            verdict = model(s, 'literature/verify_claim', {'heading': heading, 'claim': paragraph.text, 'evidence': cited}, Verdict)
        outcomes.append({'section_index': si, 'paragraph_index': pi, **verdict.model_dump()})
    accepted = [(o['section_index'], o['paragraph_index']) for o in outcomes if o['status'] == 'supported']
    feedback = [o for o in outcomes if o['status'] != 'supported']
    gaps = [{'query': o['research_query'], 'route': o['research_route'], 'reason': o['reason']} for o in outcomes if o['status'] == 'missing_evidence']
    return {'accepted': accepted, 'rejection_feedback': feedback, 'outcomes': outcomes, 'research_gaps': gaps}


def _reference(e):
    level = e.get('source_level', '')
    if level in ('web_snippet', 'web_content'):
        label = {'web_snippet': '搜索摘要', 'web_content': '网页文本'}.get(level, level)
        anchor = f" · Markdown 字符区间 {e['char_start']}–{e['char_end']}（从 0 开始）" if e.get('char_start') is not None and e.get('char_end') is not None else ''
        return f"[{e['id']}] {e.get('title', '')} · {label} · {e.get('provider', '')} · {e.get('original_url', '')} · 获取于 {e.get('retrieved_at', '')}" + anchor
    if e.get('version_id'):
        return f"[{e['id']}] {e.get('title', '')} · 原文版本 {e['version_id']}，第 {e.get('page', '')} 页"
    return f"[{e['id']}] {e.get('title', '')} · 论文摘要" + (f" · {e['original_url']}" if e.get('original_url') else '')



def _safe_direction(value):
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', str(value))
    value = re.sub(r'https?://\S+|<[^>]*>', '', value)
    value = re.sub(r'[`*_#\[\]\r\n]+', ' ', value)
    return ' '.join(value.split())[:150]


def render_partial(state, reason):
    """Render collected sources and coverage without presenting unverified conclusions."""
    research = state['research']
    allowed = set(research.get('paper_ids') or []) if research['source'] == 'collection' else None
    kept = [e for e in state.get('evidence', []) if allowed is None or e.get('paper_id') in allowed]
    lines = ['# 阶段性研究结果', _safe_direction(reason), '审查未完成或证据不足，尚未生成核验结论。']
    review = state.get('review_result')
    pending = list(getattr(review, 'gaps', [])) if review else list(state.get('directions', []))
    pending += [gap.get('query', '') for gap in state.get('research_gaps', []) if gap.get('query')]
    if review and not getattr(review, 'sufficient', False):
        pending += [step.direction for step in getattr(review, 'next_steps', [])]
    for label, values in [('已核查方向', getattr(review, 'coverage', []) if review else []),
                          ('未覆盖方向', list(dict.fromkeys(pending)))]:
        for value in values[:5]:
            clean = _safe_direction(value)
            if clean: lines.append(label + '：' + clean + '。')
    for warning, text in [('web_search_failed', '网页检索部分或全部失败，结果不完整，请稍后重试。'),
                          ('web_page_read_failed', '网页正文读取部分失败，结果可能不完整。'),
                          ('paper_search_failed', '论文外部检索部分失败，结果可能不完整。')]:
        if warning in state.get('warnings', []): lines.append(text)
    if research['source'] == 'web' and not kept and not state.get('warnings'):
        lines.append('本次网页检索未找到可用文本证据。')
    if kept:
        lines.append('## 已收集资料\n' + '\n'.join(_reference(e) for e in kept))
    else:
        lines.append('本次没有可列出的资料。')
    return {'content': '\n\n'.join(lines), 'evidence': kept}

def render(s):
    if s.get('draft_format_failed'):
        result = render_partial(s, '报告格式校验失败，本次仅提供已收集资料。')
        return {**result, 'research_gaps': []}
    accepted = set(map(tuple, s.get('accepted', [])))
    if not accepted:
        result = render_partial(s, '本次没有通过核验的结论。')
        return {**result, 'research_gaps': s.get('research_gaps', [])}
    review = s.get('review_result')
    has_verified = any(position[0] >= 0 for position in s.get('accepted', [])) and bool(s.get('evidence'))
    partial = (not has_verified or bool(s.get('research_gaps'))
               or any(o.get('status') == 'missing_evidence' for o in s.get('outcomes', []))
               or review is None or not review.sufficient or bool(review.gaps))
    lines = ['# 阶段性研究结果' if partial else '# 研究报告']
    used = set()
    for si, section in enumerate(s['draft'].sections):
        paragraphs = [p for pi, p in enumerate(section.paragraphs) if (si, pi) in accepted]
        if not paragraphs: continue
        lines.append('## ' + section.heading)
        for p in paragraphs:
            used.update(p.citations)
            lines.append(p.text + ' ' + ' '.join(f'[{i}]' for i in p.citations))
    limitations = [p for pi, p in enumerate(s['draft'].limitations) if (-1, pi) in accepted]
    if limitations:
        lines.append('## 局限')
        for p in limitations:
            used.update(p.citations)
            lines.append(p.text + ' ' + ' '.join(f'[{i}]' for i in p.citations))
    allowed = set(s['research'].get('paper_ids') or []) if s['research']['source'] == 'collection' else None
    kept = [e for e in s['evidence'] if (e['id'] in used or not has_verified) and (allowed is None or e.get('paper_id') in allowed)]
    lines.append('## 范围与待核查' if partial else '## 证据范围')
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
    for status, label in [('unsupported', '不受支持'), ('qualified', '待限定'), ('missing_evidence', '缺少证据')]:
        count = sum(o['status'] == status for o in s.get('outcomes', []))
        if count: lines.append(f'已省略{count}段（{label}）。')
    review = s.get('review_result')
    if s.get('forced_reason') and (not review or not review.sufficient):
        lines.append('研究在预算或流程边界处结束，结论仍需进一步核查。')
    warnings = s.get('warnings', [])
    if 'web_search_failed' in warnings:
        lines.append('网页检索部分或全部失败，结果不完整，请稍后重试。')
    if 'web_page_read_failed' in warnings:
        lines.append('网页正文读取部分失败，结果可能不完整。')
    if any(w not in ('web_search_failed', 'web_page_read_failed') for w in warnings):
        lines.append('论文外部检索部分失败，结果可能不完整。')
    if s['research']['source'] == 'web' and not s['evidence'] and not warnings:
        lines.append('本次网页检索未找到可用文本证据。')
    gaps = getattr(review, 'gaps', []) if review else []
    if review and not review.sufficient:
        lines.append('现有证据尚不足以全面回答问题。')
    steps = getattr(review, 'next_steps', []) if review else []
    directions = [g['query'] for g in s.get('research_gaps', []) if g.get('query')] + (([step.direction for step in steps] or gaps) if review and (not review.sufficient or gaps) else [])
    for direction in directions[:3]:
        clean = _safe_direction(direction)
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
        payload = {'question': s['research']['query'], 'history': s.get('history', []), 'directions': s.get('directions', []), 'verification_gaps': s.get('verification_gaps', []), 'evidence': s['evidence'], 'review': review.model_dump() if review else None}
        try:
            return {'draft': model(s, 'literature/synthesize', payload, Draft), 'empty_retried': s.get('empty_retried', False)}
        except StructuredOutputError:
            return {'draft_format_failed': True, 'research_gaps': []}
    def retry_empty(s):
        return {**draft(s), 'empty_retried': True}
    def check(s):
        if on_stage: on_stage('verifying')
        return verify(s, model)
    def revise(s):
        if on_stage: on_stage('synthesizing')
        feedback = [o for o in s['outcomes'] if o['status'] == 'qualified']
        payload = {'question': s['research']['query'], 'draft': s['draft'].model_dump(), 'feedback': feedback, 'evidence': s['evidence'], 'verification_gaps': s.get('verification_gaps', [])}
        patches = model(s, 'literature/revise', payload, PatchSet)
        target = {(o['section_index'], o['paragraph_index']) for o in feedback}
        updated = s['draft'].model_copy(deep=True)
        seen = set()
        for patch in patches.patches:
            position = (patch.section_index, patch.paragraph_index)
            if position not in target or position in seen: continue
            seen.add(position)
            claim = Claim(text=patch.text, citations=patch.citations)
            if patch.section_index == -1: updated.limitations[patch.paragraph_index] = claim
            else: updated.sections[patch.section_index].paragraphs[patch.paragraph_index] = claim
        return {'draft': updated, 'revised': True, 'patched_positions': list(seen)}
    def finalize(s):
        rejected = [{'section_index': o['section_index'], 'paragraph_index': o['paragraph_index'], 'reason': o['reason']} for o in s.get('outcomes', []) if o['status'] == 'unsupported']
        if rejected:
            diagnostic_id = record_failure(kind='report_verification', payload={'rejected': rejected}, schema=Verdict.model_json_schema())
            logger.info('report_verification diagnostic_id=%s count=%d', diagnostic_id, len(rejected))
        return render(s)
    def after_draft(s):
        if s.get('draft_format_failed'):
            return 'render'
        if s['evidence'] and not s['draft'].sections and not s['draft'].limitations and not s.get('empty_retried'):
            return 'retry_empty'
        return 'verify'

    def after_retry(s):
        return 'render' if s.get('draft_format_failed') else 'verify'

    graph = StateGraph(ReportState)
    graph.add_node('draft', draft)
    graph.add_node('retry_empty', retry_empty)
    graph.add_node('verify', check)
    graph.add_node('revise', revise)
    graph.add_node('render', finalize)
    graph.add_edge(START, 'draft')
    graph.add_conditional_edges('draft', after_draft)
    graph.add_conditional_edges('retry_empty', after_retry)
    graph.add_conditional_edges('verify', lambda s: 'revise' if any(o['status'] == 'qualified' for o in s['outcomes']) and not s.get('revised') else 'render')
    graph.add_edge('revise', 'verify')
    graph.add_edge('render', END)
    return graph.compile()
