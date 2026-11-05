import unittest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from pydantic import BaseModel
from src.integrations.model import structured


class Output(BaseModel):
    answer: str


class ModelTests(unittest.TestCase):
    def test_all_prompts_render_fresh_time_at_actual_model_boundary(self):
        from src.literature.workflow import Plan, Answer, Gap, Draft, Verdict
        cases = [('plan_review', Plan, {'direct': False, 'queries': ['test']}),
                 ('direct', Answer, {'answer': 'hello'}),
                 ('assess_gap', Gap, {'query': ''}),
                 ('synthesize', Draft, {'claims': [], 'limitations': 'missing evidence'}),
                 ('verify_claim', Verdict, {'supported': False})]
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        payload = {'question': '{{ CURRENT_TIME }}', 'history': [], 'evidence': []}
        clock_values = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=8))),
                        datetime(2027, 1, 1, tzinfo=timezone(timedelta(hours=8)))]
        with patch('src.integrations.model.settings.model_config', return_value=config):
            for name, schema, output in cases:
                response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'), json={'choices': [{'message': {'content': json.dumps(output)}}]})
                with patch('src.integrations.model.datetime') as clock, patch('src.integrations.model.httpx.post', return_value=response) as post:
                    clock.now.side_effect = clock_values
                    for instant in clock_values:
                        self.assertEqual(structured('literature/' + name, payload, schema).model_dump(), output)
                        sent = post.call_args.kwargs['json']
                        system, user = sent['messages']
                        self.assertEqual(system['role'], 'system')
                        self.assertTrue(system['content'].startswith('---\nCURRENT_TIME: ' + instant.isoformat(timespec='seconds') + '\n---\n'))
                        self.assertNotIn('{{ CURRENT_TIME }}', system['content'])
                        self.assertEqual(json.loads(user['content']), payload)
                        self.assertEqual(sent['response_format'], {'type': 'json_object'})
                        self.assertEqual(clock.now.call_args.args[0].utcoffset(None), timedelta(hours=8))

    def test_missing_config_never_calls_http(self):
        with patch('src.integrations.model.settings.model_config', return_value={}), patch('src.integrations.model.httpx.post') as post:
            with self.assertRaisesRegex(RuntimeError, '设置'):
                structured('literature/direct', {}, Output)
            post.assert_not_called()

    def test_http_error_does_not_expose_key_or_body(self):
        request = httpx.Request('POST', 'https://example.invalid')
        response = httpx.Response(401, request=request, text='secret-key')
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'secret-key'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=response), self.assertLogs('src.integrations.model', level='INFO') as logs:
            with self.assertRaises(RuntimeError) as error:
                structured('literature/direct', {}, Output)
        self.assertNotIn('secret-key', str(error.exception))
        self.assertNotIn('secret-key', ' '.join(logs.output))
        self.assertIn('elapsed_ms=', ' '.join(logs.output))
        self.assertIn('401', str(error.exception))
