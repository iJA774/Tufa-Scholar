"""Test-only API with a deterministic simulated OpenAlex search result."""
from apps.api.main import app
import src.literature.retrieval as retrieval


def fixture_search(query, limit=20):
    return [{
        'title': 'Attention fixture paper', 'authors': ['Fixture Author'], 'year': 2017,
        'doi': 'https://doi.org/10.1234/ATTENTION',
        'arxiv_id': 'https://arxiv.org/abs/1706.03762v2',
        'abstract': 'Simulated external search result for UI testing.',
        'source': 'fixture', 'open_access': True, 'pdf_url': None,
        'original_url': 'https://arxiv.org/abs/1706.03762v2',
        'source_level': 'abstract', 'openalex_id': 'fixture:attention',
    }] if 'attention' in query.lower() else []

retrieval.search_works = fixture_search
