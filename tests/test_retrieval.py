import json
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import httpx

from src.integrations.openalex import search_works
from src.literature.retrieval import rank_candidates, retrieve, retrieve_collection
from src.persistence.storage import initialize_storage


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.environ = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.environ.start()
        initialize_storage()

    def tearDown(self):
        self.environ.stop()
        self.tmp.cleanup()

    def test_openalex_response_and_mapping(self):
        fixture = Path(__file__).parent / 'fixtures' / 'openalex_works.json'
        response = httpx.Response(200, json=json.loads(fixture.read_text(encoding='utf-8')), request=httpx.Request('GET', 'https://api.openalex.org/works'))
        with patch('src.integrations.openalex.httpx.get', return_value=response) as call:
            result = search_works('Attention Is All You Need')
        self.assertEqual(call.call_args.kwargs['params']['search'], 'Attention Is All You Need')
        self.assertEqual(result[0]['title'], 'Attention Is All You Need')
        self.assertEqual(result[0]['authors'], ['Ashish Vaswani'])
        self.assertEqual(result[0]['abstract'], 'Attention models')
        self.assertEqual(result[0]['arxiv_id'], '1706.03762')
        self.assertEqual(result[0]['source_level'], 'abstract')

    def test_fusion_deduplicates_and_preserves_source_ranks(self):
        local = [{'title': 'local', 'doi': '10.1/ABC'}, {'title': 'other', 'doi': '10.1/other'}]
        remote = [{'title': 'remote duplicate', 'doi': 'https://doi.org/10.1/abc'}]
        with patch('src.literature.retrieval.repository.local_search', return_value=local), patch('src.literature.retrieval.search_works', return_value=remote):
            results = retrieve(1, 'test', 'all')['results']
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['retrieval_sources'], [{'source': 'local', 'rank': 1}, {'source': 'openalex', 'rank': 1}])

    def test_api_key_is_sent_in_header_not_url(self):
        fixture = Path(__file__).parent / 'fixtures' / 'openalex_works.json'
        response = httpx.Response(200, json=json.loads(fixture.read_text(encoding='utf-8')), request=httpx.Request('GET', 'https://api.openalex.org/works'))
        with patch.dict(os.environ, {'OPENALEX_API_KEY': 'test-key'}), patch('src.integrations.openalex.httpx.get', return_value=response) as call:
            search_works('attention')
        self.assertEqual(call.call_args.kwargs['headers']['Authorization'], 'Bearer test-key')
        self.assertNotIn('test-key', str(call.call_args.kwargs['params']))

    def test_http_error_does_not_expose_secret(self):
        response = httpx.Response(403, text='test-secret', request=httpx.Request('GET', 'https://api.openalex.org/works'))
        with patch.dict(os.environ, {'OPENALEX_API_KEY': 'test-secret'}), patch('src.integrations.openalex.httpx.get', return_value=response), self.assertLogs('src.integrations.openalex', level='INFO') as logs:
            with self.assertRaises(RuntimeError) as caught:
                search_works('attention')
        self.assertIn('403', str(caught.exception))
        self.assertNotIn('test-secret', str(caught.exception))
        self.assertNotIn('test-secret', ' '.join(logs.output))
        self.assertIn('elapsed_ms=', ' '.join(logs.output))

    def test_all_keeps_local_results_on_external_failure(self):
        with patch('src.literature.retrieval.repository.local_search', return_value=[{'title': 'local'}]), patch('src.literature.retrieval.search_works', side_effect=RuntimeError('OpenAlex unavailable')):
            result = retrieve(1, 'attention', 'all')
            self.assertEqual(result['results'][0]['title'], 'local')
            self.assertEqual(result['warnings'], ['OpenAlex unavailable'])
            with self.assertRaises(RuntimeError):
                retrieve(1, 'attention', 'openalex')

    def test_candidate_rank_fusion_merges_ids_and_uses_one_vote_per_query(self):
        query_results = {
            'same query': [
                {'paper': {'id': 1, 'doi': '10.1234/shared', 'title': 'Shared'}, 'rank': 1},
                {'paper': {'id': 2, 'doi': 'https://doi.org/10.1234/SHARED', 'title': 'Alias'}, 'rank': 2},
                {'paper': {'id': 3, 'doi': '10.1234/other', 'title': 'Other'}, 'rank': 3},
            ],
            'second query': [
                {'paper': {'id': 4, 'doi': '10.1234/shared', 'title': 'Shared'}, 'rank': 2},
            ],
        }
        ranked = rank_candidates(query_results, 'all')
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]['doi'], '10.1234/shared')
        self.assertEqual({item['title'] for item in ranked}, {'Shared', 'Other'})

    def test_candidate_identity_respects_source_and_keeps_full_pool(self):
        papers = [
            {'id': i, 'title': f'paper {i}', 'doi': f'10.1234/{i}', 'abstract': f'evidence {i}'}
            for i in range(1, 9)
        ]
        with patch('src.literature.retrieval.repository.project_evidence', return_value=papers), patch(
            'src.literature.retrieval.repository.local_search', return_value=papers
        ):
            result = retrieve_collection(1, 'topic', list(range(1, 9)))
        self.assertEqual(len(result['results']), 8)
        self.assertEqual({paper['id'] for paper in result['results']}, set(range(1, 9)))

        web_rows = {
            'first': [{'paper': {'url': 'https://example.org/a', 'title': 'One'}, 'rank': 1}],
            'second': [{'paper': {'url': 'https://example.org/b', 'title': 'Two'}, 'rank': 1}],
        }
        self.assertEqual(len(rank_candidates(web_rows, 'web')), 2)


if __name__ == '__main__':
    unittest.main()
