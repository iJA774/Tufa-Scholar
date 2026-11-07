import unittest
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from pydantic import BaseModel
from src.integrations.model import structured


class Output(BaseModel):
    answer: str


class ModelTests(unittest.TestCase):
    def test_all_prompts_render_fresh_time_at_actual_model_boundary(self):
        cases = ['runtime/route', 'literature/research', 'literature/review',
                 'literature/synthesize', 'literature/verify_claim', 'literature/revise']
        output = {'answer': 'ok'}
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        payload = {'question': '{{ CURRENT_TIME }}', 'history': [], 'evidence': []}
        clock_values = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=8))),
                        datetime(2027, 1, 1, tzinfo=timezone(timedelta(hours=8)))]
        with patch('src.integrations.model.settings.model_config', return_value=config):
            for name in cases:
                response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'), json={'choices': [{'message': {'content': json.dumps(output)}}]})
                with patch('src.integrations.model.datetime') as clock, patch('src.integrations.model.httpx.post', return_value=response) as post:
                    clock.now.side_effect = clock_values
                    for instant in clock_values:
                        self.assertEqual(structured(name, payload, Output).model_dump(), output)
                        sent = post.call_args.kwargs['json']
                        system, user = sent['messages']
                        self.assertEqual(system['role'], 'system')
                        self.assertTrue(system['content'].startswith('---\nCURRENT_TIME: ' + instant.isoformat(timespec='seconds') + '\n---\n'))
                        self.assertNotIn('{{ CURRENT_TIME }}', system['content'])
                        self.assertEqual(json.loads(user['content']), payload)
                        self.assertEqual(sent['response_format'], {'type': 'json_object'})
                        self.assertEqual(clock.now.call_args.args[0].utcoffset(None), timedelta(hours=8))

    def test_template_loads_outside_repository_root(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'),
                                 json={'choices': [{'message': {'content': json.dumps({'answer': 'ok'})}}]})
        original_cwd = Path.cwd()
        try:
            os.chdir(Path(__file__).resolve().parent)
            with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=response) as post:
                self.assertEqual(structured('runtime/route', {'question': 'test'}, Output).answer, 'ok')
            self.assertTrue(post.call_args.kwargs['json']['messages'][0]['content'].startswith('---\nCURRENT_TIME: '))
        finally:
            os.chdir(original_cwd)

    def test_missing_config_never_calls_http(self):
        with patch('src.integrations.model.settings.model_config', return_value={}), patch('src.integrations.model.httpx.post') as post:
            with self.assertRaisesRegex(RuntimeError, '设置'):
                structured('runtime/route', {}, Output)
            post.assert_not_called()

    def test_http_error_does_not_expose_key_or_body(self):
        request = httpx.Request('POST', 'https://example.invalid')
        response = httpx.Response(401, request=request, text='secret-key')
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'secret-key'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=response), self.assertLogs('src.integrations.model', level='INFO') as logs:
            with self.assertRaises(RuntimeError) as error:
                structured('runtime/route', {}, Output)
        self.assertNotIn('secret-key', str(error.exception))
        self.assertNotIn('secret-key', ' '.join(logs.output))
        self.assertIn('elapsed_ms=', ' '.join(logs.output))
        self.assertIn('401', str(error.exception))
