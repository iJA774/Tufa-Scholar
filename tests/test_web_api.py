import csv
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.main import app
from src.persistence import repository as repo


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.directory.name}, clear=True)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post('/projects', json={'name': '网页研究'}).json()['id']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def test_search_read_only_validation_and_failure(self):
        result = {'title': '研究网页', 'url': 'https://example.org/research', 'content': '测试证据',
                  'source_level': 'web_snippet', 'provider': 'duckduckgo', 'retrieved_at': '2026-11-06T00:00:00+00:00'}
        path = f'/projects/{self.project}/web-search'
        with patch('src.tools.search.search_web', return_value=[result]) as search:
            self.assertEqual(self.client.get(path, params={'q': '  测试  '}).json()['results'], [result])
            search.assert_called_once_with('测试', limit=5)
            self.assertEqual(self.client.get(path, params={'q': ' '}).status_code, 422)
            self.assertEqual(self.client.get(path, params={'q': 'x' * 2001}).status_code, 422)
            self.assertEqual(self.client.get('/projects/999/web-search', params={'q': 'test'}).status_code, 404)
            self.assertEqual(search.call_count, 1)
        self.assertEqual(self.client.get(f'/projects/{self.project}/papers').json(), [])
        with patch('src.tools.search.search_web', side_effect=RuntimeError('搜索服务暂不可用')):
            response = self.client.get(path, params={'q': 'test'})
            self.assertEqual(response.status_code, 503)
            self.assertIn('暂不可用', response.json()['detail'])

    def test_web_question_retry_and_evidence_export(self):
        response = self.client.post(f'/projects/{self.project}/questions', json={'question': '查询网页', 'source': 'web'})
        self.assertEqual(response.status_code, 201)
        run_id = response.json()['run_id']
        task = repo.claim_task()
        self.assertEqual(task['source'], 'web')
        repo.fail_task(run_id, '模拟失败')
        retry = self.client.post(f'/runs/{run_id}/retry').json()
        self.assertEqual(retry['source'], 'web')
        task = repo.claim_task()
        evidence = [{'id': 'w1', 'title': '网页', 'source_level': 'web_content', 'text': '测试正文',
                     'original_url': 'https://example.org', 'provider': 'tavily',
                     'retrieved_at': '2026-11-06T00:00:00+00:00', 'published_at': '2026-10-01'}]
        report = repo.finish_chat_task(task['id'], '测试回答 [w1]', evidence)
        detail = self.client.get(f"/reports/{report['report_id']}").json()
        self.assertEqual(detail['evidence'], evidence)
        exported = self.client.get(f"/reports/{report['report_id']}/evidence.csv").text.lstrip('\ufeff')
        rows = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(rows[0]['provider'], 'tavily')
        self.assertEqual(rows[0]['retrieved_at'], evidence[0]['retrieved_at'])
        self.assertEqual(rows[0]['original_url'], 'https://example.org')

    def test_all_provider_settings_through_api_and_no_secret_echo(self):
        fields = ['tavily_api_key', 'infoquest_api_key', 'brave_search_api_key', 'serper_api_key']
        for engine in ['duckduckgo', 'tavily', 'infoquest', 'brave_search', 'serper', 'arxiv', 'searx', 'wikipedia']:
            response = self.client.put('/settings', json={'web_search_engine': engine, **{name: 'test-secret-' + name for name in fields}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['web_search']['engine'], engine)
            self.assertNotIn('test-secret-', response.text)
        self.assertEqual(self.client.put('/settings', json={'web_search_engine': 'bad'}).status_code, 422)
        response = self.client.put('/settings', json={'tavily_api_key': {'secret': 'never-echo'}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('never-echo', response.text)
        response = self.client.put('/settings', json={f'clear_{name}': True for name in fields})
        self.assertTrue(all(not p['key_configured'] for p in response.json()['web_search']['providers']))
