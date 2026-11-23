"""Build model responses using the workflow's text control protocol."""
import json
from types import SimpleNamespace


def plan(*, requirements=(), plan=()):
    return '\n'.join([
        '# 研究计划', '', '## 研究要求',
        *(f'- {item}' for item in requirements), '', '## 研究步骤',
        *(f'- {item}' for item in plan),
    ])


def route(source='paper', action='research', completion_confirmed=False, *, initial=False):
    arguments = {'source': source}
    if not initial:
        arguments.update(action=action, completion_confirmed=completion_confirmed)
    return SimpleNamespace(
        action='tool', tool_name='select_research_route',
        arguments=arguments,
    )


def review(**fields):
    fields.pop('review', None)
    data = {'sufficient': False, 'covered_requirements': [], 'gaps': [], 'next_steps': [],
            'unresolved_contradictions': [], 'evidence_reliability_confirmed': False,
            'important_conclusions_cross_validated': False}
    data.update(fields)
    return '研究审查\n\n```tufa-control\n' + json.dumps(data, ensure_ascii=False) + '\n```'


def claim(text, citations=None):
    if citations:
        return text + ' ' + ''.join(f'[[{item}]]' for item in citations)
    return '> 局限：' + text


def section(heading, paragraphs):
    return '## ' + heading + '\n\n' + '\n\n'.join(paragraphs)


def draft(sections=None, limitations=None, title=None):
    return '\n\n'.join(([f'# {title}'] if title else []) + (sections or []) + (limitations or []))
