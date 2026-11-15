"""Build model responses using the workflow's text control protocol."""
import json


def plan(**fields):
    data = {'requirements': [], 'plan': [], 'source': 'paper', 'action': 'research',
            'completion_confirmed': False, 'rationale': ''}
    data.update(fields)
    return '研究计划\n\n```tufa-control\n' + json.dumps(data, ensure_ascii=False) + '\n```'


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
