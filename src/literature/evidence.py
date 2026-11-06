"""Extract source-grounded evidence from project papers and web results."""
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from pypdf import PdfReader

from src.persistence import repository


def collect_evidence(project_id, source, paper_ids, query, papers):
    if source == 'web':
        items = papers
        found = []
        seen = set()
        for item in items:
            text = item.get('content', '').strip()[:1200]
            if not text:
                continue
            parts = urlsplit(item['url'])
            url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))
            if url in seen:
                continue
            seen.add(url)
            entry = {
                'id': 'w' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:16],
                'title': item['title'], 'original_url': item['url'],
                'source_level': item['source_level'], 'provider': item['provider'],
                'retrieved_at': item['retrieved_at'], 'text': text,
            }
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
            for v in record['versions']:
                if v['text_status'] != 'text_available':
                    continue
                reader = PdfReader(v['path'])
                terms = [t.lower() for t in re.findall(r'\w+', query) if len(t) > 1]
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
    return found
