import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.openalex import search_works
from src.literature.retrieval import retrieve


class RetrievalTests(unittest.TestCase):
    def test_openalex_response_and_mapping(self):
        fixture = Path(__file__).parent / 'fixtures' / 'openalex_works.json'
        with patch('src.integrations.openalex.urlopen', return_value=io.BytesIO(fixture.read_bytes())) as call:
            result = search_works('Attention Is All You Need')
        self.assertIn('search=Attention+Is+All+You+Need', call.call_args.args[0].full_url)
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
        with patch.dict(os.environ, {'OPENALEX_API_KEY': 'test-key'}), patch('src.integrations.openalex.urlopen', return_value=io.BytesIO(fixture.read_bytes())) as call:
            search_works('attention')
        request = call.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
        self.assertNotIn('test-key', request.full_url)


if __name__ == '__main__':
    unittest.main()
