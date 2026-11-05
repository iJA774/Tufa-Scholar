import os
import tempfile
import unittest
import httpx
from unittest.mock import patch
from fastapi.testclient import TestClient
from apps.api.main import app
from src.integrations.openalex import search_works


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def test_save_blank_clear_and_no_secret_in_responses(self):
        c = self.client
        body = {'openalex_api_key':'alex-secret', 'openai_api_key':'model-secret', 'openai_base_url':'https://model.example/v1', 'openai_model':'example-model'}
        response = c.put('/settings', json=body)
        self.assertEqual(response.status_code, 200)
        for secret in ('alex-secret', 'model-secret'):
            self.assertNotIn(secret, response.text)
            self.assertNotIn(secret, c.get('/settings').text)
        self.assertEqual(response.json()['openalex'], {'key_configured':True, 'key_source':'local'})
        self.assertEqual(response.json()['model']['base_url'], 'https://model.example/v1')
        result = c.put('/settings', json={'openalex_api_key':'', 'openai_api_key':None}).json()
        self.assertTrue(result['openalex']['key_configured'])
        self.assertTrue(result['model']['key_configured'])
        result = c.put('/settings', json={'clear_openalex_api_key':True, 'clear_openai_api_key':True, 'openai_base_url':'', 'openai_model':''}).json()
        self.assertFalse(result['openalex']['key_configured'])
        self.assertFalse(result['model']['key_configured'])
        self.assertIsNone(result['model']['base_url'])

    def test_environment_override_and_worker_reads_changed_local_key(self):
        c = self.client
        c.put('/settings', json={'openalex_api_key':'first-key', 'openai_api_key':'saved-key', 'openai_base_url':'http://localhost:8080/v1','openai_model':'saved'})
        def request_key():
            response = httpx.Response(200, json={'results': []}, request=httpx.Request('GET', 'https://api.openalex.org/works'))
            with patch('src.integrations.openalex.httpx.get', return_value=response) as call:
                search_works('test')
                return call.call_args.kwargs['headers']['Authorization']
        self.assertEqual(request_key(), 'Bearer first-key')
        c.put('/settings', json={'openalex_api_key':'second-key'})
        self.assertEqual(request_key(), 'Bearer second-key')
        with patch.dict(os.environ, {'OPENALEX_API_KEY':'env-alex','OPENAI_API_KEY':'env-model','OPENAI_BASE_URL':'https://env.example/v1','OPENAI_MODEL':'env-name'}):
            state = c.put('/settings', json={'clear_openalex_api_key':True, 'clear_openai_api_key':True}).json()
            self.assertEqual(state['openalex']['key_source'], 'environment')
            self.assertEqual(state['model']['key_source'], 'environment')
            self.assertEqual(state['model']['base_url'], 'https://env.example/v1')
            self.assertEqual(state['model']['model'], 'env-name')
            self.assertEqual(request_key(), 'Bearer env-alex')
        self.assertFalse(c.get('/settings').json()['openalex']['key_configured'])
        self.assertFalse(c.get('/settings').json()['model']['key_configured'])

    def test_invalid_request_never_echoes_secret(self):
        c = self.client
        response = c.put('/settings', json={'openalex_api_key':'secret-to-hide','openai_base_url':'ftp://bad'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('secret-to-hide', response.text)
        response = c.put('/settings', json={'openalex_api_key':{'bad':'secret-to-hide'}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('secret-to-hide', response.text)
        self.assertFalse(c.get('/settings').json()['openalex']['key_configured'])

if __name__ == '__main__': unittest.main()
