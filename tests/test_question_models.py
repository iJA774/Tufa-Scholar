import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from apps.api.main import app
from src.persistence import repository
from src.agents.literature import run
from src.integrations.model import discover, structured
from src.agents.literature import Route
from src.persistence.storage import initialize_storage


class QuestionModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.env.start()
        self.client = TestClient(app).__enter__()
        self.client.put('/settings', json={'openai_base_url': 'https://example.invalid/v1', 'openai_api_key': 'private-secret', 'openai_model': 'old'})
        self.project = self.client.post('/projects', json={'name': 'test'}).json()['id']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def response(self, data):
        return httpx.Response(200, request=httpx.Request('GET', 'https://example.invalid/v1/models'), json={'data': data})

    def ask(self, **options):
        return self.client.post(f'/projects/{self.project}/questions', json={'question': 'hello', 'model_name': 'selected', **options})

    def test_supplier_options_snapshot_retry_and_actual_model_payload(self):
        rows = [{'id': 'selected', 'effort': {'supported_levels': ['low', 'max'], 'default_level': 'max'}}]
        with patch('src.integrations.model.httpx.get', return_value=self.response(rows)):
            catalog = self.client.get('/models')
            self.assertEqual(catalog.json()['models'][0]['reasoning_efforts'], ['low', 'max'])
            self.assertNotIn('private-secret', catalog.text)
            self.assertEqual(self.ask(reasoning_effort='medium').status_code, 422)
            result = self.ask(reasoning_effort='max')
            self.assertEqual(result.status_code, 201)
        task = repository.get_task(result.json()['run_id'])
        self.assertEqual(task['model_name'], 'selected')
        self.client.put('/settings', json={'openai_model': 'changed'})
        repository.fail_task(task['id'], 'test')
        retry = repository.retry_task(task['id'])
        self.assertEqual((retry['model_name'], retry['reasoning_effort']), ('selected', 'max'))
        calls = []
        def post(url, **kwargs):
            calls.append(kwargs['json'])
            result = {'action': 'direct', 'answer': 'hello'}
            return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': json.dumps(result)}}]})
        with patch('src.integrations.model.httpx.post', side_effect=post):
            content, _ = run({k: retry[k] for k in ('project_id', 'query', 'source', 'paper_ids', 'model_name', 'reasoning_effort')}, history=[])
        self.assertIn('hello', content)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(c['model'] == 'selected' and c['reasoning_effort'] == 'max' for c in calls))
        initialize_storage()
        self.assertEqual(repository.get_task(retry['id'])['model_name'], 'selected')

    def test_missing_metadata_and_unavailable_catalog_use_three_levels(self):
        with patch('src.integrations.model.httpx.get', return_value=self.response([{'id': 'selected'}])):
            for level in ('low', 'medium', 'high'):
                self.assertEqual(self.ask(reasoning_effort=level).status_code, 201)
            self.assertEqual(self.ask(reasoning_effort='max').status_code, 422)
        with patch('src.integrations.model.httpx.get', side_effect=httpx.ConnectError('private-secret')):
            self.assertEqual(self.ask(reasoning_effort='medium').status_code, 201)

    def test_default_omits_effort_and_environment_model_wins(self):
        with patch.dict(os.environ, {'OPENAI_MODEL': 'env-model'}):
            result = self.ask()
        task = repository.get_task(result.json()['run_id'])
        self.assertEqual(task['model_name'], 'env-model')
        response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'), json={'choices': [{'message': {'content': '{"action":"direct","answer":"hello"}'}}]})
        with patch('src.integrations.model.httpx.post', return_value=response) as post:
            structured('runtime/route', {}, Route, model_options=task)
        self.assertNotIn('reasoning_effort', post.call_args.kwargs['json'])

    def test_metadata_normalization_and_secret_redaction(self):
        with patch('src.integrations.model.httpx.get', return_value=self.response([{'id': 'x', 'effort': {'supported_levels': ['low', 'low', 1], 'default_level': 'unknown'}}])):
            self.assertEqual(discover('https://example.invalid/v1', None, details=True), [{'id': 'x', 'reasoning_efforts': ['low'], 'default_effort': None}])
        with patch('src.integrations.model.httpx.get', return_value=self.response([{'id': 'x', 'effort': {'supported_levels': ['private-secret']}}])):
            result = self.client.get('/models')
        self.assertEqual(result.status_code, 502)
        self.assertNotIn('private-secret', result.text)
