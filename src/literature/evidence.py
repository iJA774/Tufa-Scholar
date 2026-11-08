"""Extract source-grounded evidence from project papers and web results."""
import hashlib
from urllib.parse import urlsplit, urlunsplit

from pypdf import PdfReader

from src.literature.keywords import query_terms
from src.persistence import repository


def collect_evidence(project_id, source, paper_ids, query, papers):
    if source == 'web':
        items = papers
        found = []
        seen = set()
        for item in items:
            raw = item.get('content', '')
            if not raw.strip():
                continue
            parts = urlsplit(item['url'])
            url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))
            offset = item.get('content_offset')
            if offset is None:
                windows = [(0, raw.strip()[:1200])]
            else:
                terms = query_terms(query)
                windows = [(start, raw[start:start + 1200]) for start in range(0, len(raw), 1000)]
                windows = sorted(windows, key=lambda pair: (-sum(term in pair[1].casefold() for term in terms), pair[0]))[:1]
            for start, text in windows:
                absolute = offset + start if offset is not None else None
                key = (url, absolute) if absolute is not None else url
                if key in seen:
                    continue
                seen.add(key)
                identifier = url if absolute is None else f'{url}\n{absolute}\n{absolute + len(text)}'
                entry = {
                    'id': 'w' + hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:16],
                    'title': item['title'], 'original_url': item['url'],
                    'source_level': item['source_level'], 'provider': item['provider'],
                    'retrieved_at': item['retrieved_at'], 'text': text,
                }
                if absolute is not None:
                    entry.update(char_start=absolute, char_end=absolute + len(text))
                if item.get('published_at'):
                    entry['published_at'] = item['published_at']
                found.append(entry)
        return found
    allowed = list(paper_ids) if source == 'collection' else None
    records = repository.project_evidence(project_id, allowed) if allowed is not None else repository.project_evidence(project_id)
    local = {p['id']: p for p in records if allowed is None or p['id'] in allowed}
    found = []
    for incoming in papers:
        record = local.get(incoming.get('id'))
        if source in {'local', 'collection'} and not record:
            continue
        p = record if record else {**incoming, 'id': None}
        if record:
            candidates = []
            terms = query_terms(query)
            for v in record['versions']:
                if v['text_status'] != 'text_available':
                    continue
                if not v['pages_indexed']:
                    try:
                        reader = PdfReader(v['path'])
                        pages = [(page.extract_text() or '').strip() for page in reader.pages]
                    except Exception:
                        continue
                    repository.index_version_pages(v['id'], pages)
                for page in repository.version_pages(v['id']):
                    text = page['text']
                    for offset in range(0, len(text), 1000):
                        fragment = text[offset:offset + 1200]
                        normalized = fragment.casefold()
                        score = sum(term in normalized for term in terms)
                        if fragment.strip():
                            candidates.append((score, v['id'], page['page'], offset, fragment))
                if candidates:
                    break
            for _, version_id, page_num, offset, text in sorted(candidates, key=lambda x: (-x[0], x[2], x[3]))[:6]:
                suffix = f"s{offset}" if offset else ''
                found.append({'id': f"p{p['id']}v{version_id}p{page_num}{suffix}", 'paper_id': p['id'], 'title': p['title'], 'source_level': 'full_text', 'version_id': version_id, 'page': page_num, 'text': text})
        if not any(e.get('paper_id') == p.get('id') and (p.get('id') is not None or e['title'] == p['title']) for e in found) and p.get('abstract'):
            found.append({'id': f"a{len(found)+1}", 'paper_id': p.get('id'), 'title': p['title'], 'source_level': 'abstract', 'version_id': None, 'page': None, 'text': p['abstract'][:1200], 'original_url': p.get('original_url')})
    return found
