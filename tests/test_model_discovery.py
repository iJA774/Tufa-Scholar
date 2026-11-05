import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from apps.api.main import app
from src.integrations.model import base_url, discover


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.put('/settings', json={'openai_base_url': 'https://example.com/v1', 'openai_api_key': 'saved-secret', 'openai_model': 'old-id'})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def request(self, body, payload=None, status=200):
        response = httpx.Response(status, json=payload if payload is not None else {'data': [{'id': 'provider/one'}, {'id': 'two'}]}, request=httpx.Request('GET', 'https://example.com/v1/models'))
        with patch('src.integrations.model.httpx.get', return_value=response) as call:
            result = self.client.post('/settings/models', json=body)
        return result, call

    def test_models_without_saving_and_key_precedence(self):
        result, call = self.request({'base_url': 'https://example.com/v1', 'api_key': 'draft-secret'})
        self.assertEqual(result.json(), {'models': ['provider/one', 'two']})
        self.assertEqual(call.call_args.args[0], 'https://example.com/v1/models')
        self.assertEqual(call.call_args.kwargs['headers']['Authorization'], 'Bearer draft-secret')
        self.assertEqual(self.client.get('/settings').json()['model']['model'], 'old-id')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'env-secret'}):
            result, call = self.request({'base_url': 'https://example.com/v1', 'api_key': 'draft-secret'})
            self.assertEqual(call.call_args.kwargs['headers']['Authorization'], 'Bearer env-secret')
            result, call = self.request({'base_url': 'https://other.example/v1'})
            self.assertEqual(call.call_args.kwargs['headers'], {})
            result, call = self.request({'base_url': 'https://other.example/v1', 'api_key': 'new-secret'})
            self.assertEqual(call.call_args.kwargs['headers']['Authorization'], 'Bearer new-secret')
        result, call = self.request({'base_url': 'https://example.com/v1'})
        self.assertEqual(call.call_args.kwargs['headers']['Authorization'], 'Bearer saved-secret')
        result, call = self.request({'base_url': 'https://example.com/v1', 'clear_local_key': True, 'api_key': 'draft-secret'})
        self.assertEqual(call.call_args.kwargs['headers'], {})
        for url in ('http://example.com/v1', 'https://example.com:444/v1'):
            result, call = self.request({'base_url': url})
            self.assertEqual(call.call_args.kwargs['headers'], {})

    def test_errors_and_validation_never_echo_secrets(self):
        for payload, status in [({'error': 'saved-secret draft-secret'}, 401), ({'data': [{'id': 'saved-secret'}]}, 200), ({'unexpected': 'draft-secret'}, 200)]:
            result, _ = self.request({'base_url': 'https://example.com/v1', 'api_key': 'draft-secret'}, payload, status)
            self.assertEqual(result.status_code, 502)
            self.assertNotIn('saved-secret', result.text)
            self.assertNotIn('draft-secret', result.text)
        with patch('src.integrations.model.httpx.get', side_effect=httpx.ReadTimeout('draft-secret')):
            result = self.client.post('/settings/models', json={'base_url': 'https://example.com', 'api_key': 'draft-secret'})
            self.assertEqual(result.status_code, 502)
            self.assertNotIn('draft-secret', result.text)
        result = self.client.post('/settings/models', json={'base_url': 'https://example.com', 'api_key': {'secret': 'draft-secret'}})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn('draft-secret', result.text)
        for url in ('', 'ftp://example.com', 'https://x:y@example.com', 'https://example.com/?secret=yes'):
            self.assertEqual(self.client.post('/settings/models', json={'base_url': url}).status_code, 422)

    def test_empty_list_and_path_rules(self):
        result, _ = self.request({'base_url': 'https://example.com/v1'}, {'data': []})
        self.assertEqual(result.json(), {'models': []})
        result, _ = self.request({'base_url': 'https://example.com/v1'}, {'data': [{'id': 'same'}, {'id': 'same'}, {'id': 'other'}]})
        self.assertEqual(result.json(), {'models': ['same', 'other']})
        self.assertEqual(base_url('https://api.openai.com/'), 'https://api.openai.com/v1')
        self.assertEqual(base_url('https://generativelanguage.googleapis.com'), 'https://generativelanguage.googleapis.com/v1beta/openai')
        self.assertEqual(base_url('https://dashscope.aliyuncs.com'), 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.assertEqual(base_url('https://api.deepseek.com'), 'https://api.deepseek.com')
        self.assertEqual(base_url('https://ark.cn-beijing.volces.com'), 'https://ark.cn-beijing.volces.com/api/v3')
        self.assertEqual(base_url('https://tokenhub.tencentmaas.com/'), 'https://tokenhub.tencentmaas.com/v1')
        self.assertEqual(base_url('https://api.siliconflow.cn'), 'https://api.siliconflow.cn/v1')
        self.assertEqual(base_url('https://api.moonshot.cn'), 'https://api.moonshot.cn/v1')
        self.assertEqual(base_url('https://workspace.cn-beijing.maas.aliyuncs.com/'), 'https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
        self.assertEqual(base_url('https://api.openai.com/custom/v2/'), 'https://api.openai.com/custom/v2')

    def test_bailian_native_fallback_pages_and_custom_paths(self):
        request = httpx.Request('GET', 'https://workspace.cn-beijing.maas.aliyuncs.com/models')
        responses = [httpx.Response(404, request=request),
                     httpx.Response(200, json={'output': {'total': 3, 'models': [{'model': 'first'}, {'model': 'second'}]}}, request=request),
                     httpx.Response(200, json={'output': {'total': 3, 'models': [{'model': 'third'}]}}, request=request)]
        with patch('src.integrations.model.httpx.get', side_effect=responses) as call:
            self.assertEqual(discover('https://workspace.cn-beijing.maas.aliyuncs.com', 'key'), ['first', 'second', 'third'])
            self.assertEqual(call.call_args_list[0].args[0], 'https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models')
            self.assertEqual(call.call_args_list[1].args[0], 'https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/models')
            self.assertEqual(call.call_args_list[2].kwargs['params']['page_no'], 2)
        with patch('src.integrations.model.httpx.get', return_value=httpx.Response(200, json={'data': [{'id': 'custom'}]}, request=request)) as call:
            self.assertEqual(discover('https://workspace.cn-beijing.maas.aliyuncs.com/custom', None), ['custom'])
            self.assertEqual(call.call_count, 1)
            self.assertTrue(call.call_args.args[0].endswith('/custom/models'))

    def test_save_selection_and_clear_old_key_on_origin_change(self):
        self.client.put('/settings', json={'openai_model': 'provider/one'})
        self.assertEqual(self.client.get('/settings').json()['model']['model'], 'provider/one')
        response = self.client.put('/settings', json={'openai_base_url': 'https://other.example/v1'})
        self.assertFalse(response.json()['model']['key_configured'])
        result, call = self.request({'base_url': 'https://other.example/v1'})
        self.assertEqual(call.call_args.kwargs['headers'], {})


if __name__ == '__main__':
    unittest.main()
