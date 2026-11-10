import unittest
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from pydantic import BaseModel
from src.integrations.model import structured, StructuredOutputError, ModelServiceError


class Output(BaseModel):
    answer: str


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.diagnostics = patch('src.integrations.model.record_failure', return_value='diagnostic-1')
        self.diagnostics_mock = self.diagnostics.start()
        self.addCleanup(self.diagnostics.stop)

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

    def test_schema_and_one_safe_correction_at_http_boundary(self):
        from src.agents.literature import Route
        config = {'base_url': 'https://example.invalid/v1', 'model': 'default', 'api_key': 'secret-key'}
        payload = {'question': 'private question'}
        for bad in ('{broken secret-key', '{"action":"bad secret-key", "private-key-secret": "secret-value"}', '{}'):
            with self.subTest(bad=bad):
                sent = []
                def post(url, **kwargs):
                    sent.append(kwargs['json'])
                    body = bad if len(sent) == 1 else '{"action":"direct","answer":"ok"}'
                    return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': body}}]})
                with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', side_effect=post), self.assertLogs('src.integrations.model', level='INFO') as logs:
                    result = structured('runtime/route', payload, Route, model_options={'model_name': 'chosen', 'reasoning_effort': 'high'})
                self.assertEqual(result.answer, 'ok')
                diagnostic = self.diagnostics_mock.call_args.kwargs
                self.assertEqual(diagnostic['response'], bad)
                self.assertEqual(diagnostic['secrets'], ('secret-key',))
                self.assertEqual(diagnostic['kind'], 'model_validation')
                self.assertEqual(len(sent), 2)
                self.assertEqual(len(sent[0]['messages']), 2)
                self.assertIn('"action"', sent[0]['messages'][0]['content'])
                self.assertIn('"required"', sent[0]['messages'][0]['content'])
                self.assertEqual(json.loads(sent[0]['messages'][1]['content']), payload)
                self.assertEqual(sent[0]['messages'], sent[1]['messages'][:2])
                self.assertIn('validation_feedback', json.loads(sent[1]['messages'][2]['content']))
                for request in sent:
                    self.assertEqual((request['model'], request['reasoning_effort']), ('chosen', 'high'))
                feedback = sent[1]['messages'][2]['content'] + ' '.join(logs.output)
                for secret in ('secret-key', 'private-key-secret', 'secret-value', 'private question'):
                    self.assertNotIn(secret, feedback)

    def test_specialist_direct_web_feedback_at_http_boundary(self):
        from src.agents.literature import Specialist
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'secret-key'}
        bad = {'action': 'read_web_page', 'url': 'https://example.org/private', 'plan': ['private-plan']}
        good = dict(bad, query='election dates')
        sent = []
        def post(url, **kwargs):
            sent.append(kwargs['json'])
            body = bad if len(sent) == 1 else good
            return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': json.dumps(body)}}]})
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', side_effect=post), self.assertLogs('src.integrations.model', level='INFO') as logs:
            result = structured('literature/research', {'question': 'private-question'}, Specialist)
        self.assertEqual(result.query, 'election dates')
        feedback = json.loads(sent[1]['messages'][2]['content'])['validation_feedback']
        self.assertEqual(feedback, [{'type': 'specialist_direct_query_required', 'path': []}])
        safe = sent[1]['messages'][2]['content'] + ' '.join(logs.output)
        for secret in ('secret-key', 'private-question', 'private-plan', 'example.org', '直读网址需提供主题词'):
            self.assertNotIn(secret, safe)
        self.assertEqual(len(sent), 2)

    def test_unknown_field_name_is_redacted_from_feedback_and_logs(self):
        from pydantic import ConfigDict
        class StrictOutput(BaseModel):
            model_config = ConfigDict(extra='forbid')
            answer: str
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        responses = [httpx.Response(200, request=request, json={'choices': [{'message': {'content': content}}]}) for content in
                     ('{"answer":"ok","secret-field-name":"secret-value"}', '{"answer":"ok"}')]
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), patch('src.integrations.model.httpx.post', side_effect=responses) as post, self.assertLogs('src.integrations.model', level='INFO') as logs:
            structured('runtime/route', {}, StrictOutput)
        feedback = post.call_args.kwargs['json']['messages'][2]['content'] + ' '.join(logs.output)
        self.assertNotIn('secret-field-name', feedback)
        self.assertNotIn('secret-value', feedback)
        self.assertIn('extra_forbidden', feedback)
        sent = post.call_args.kwargs['json']['messages']
        self.assertEqual(json.loads(sent[2]['content'])['validation_feedback'], [{'type': 'extra_forbidden', 'path': ['*']}])
        self.assertIn('隐私规则省略', sent[0]['content'])
        self.assertIn('未声明字段', sent[0]['content'])
        self.assertEqual(post.call_count, 2)

    def test_nested_array_index_survives_safe_feedback(self):
        from pydantic import ConfigDict
        class Item(BaseModel):
            model_config = ConfigDict(extra='forbid')
            value: str
        class Nested(BaseModel):
            items: list[Item]
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        responses = [httpx.Response(200, request=request, json={'choices': [{'message': {'content': content}}]}) for content in
                     ('{"items":[{"value":"ok","private-key":"secret"}]}', '{"items":[{"value":"ok"}]}')]
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), patch('src.integrations.model.httpx.post', side_effect=responses) as post:
            structured('runtime/route', {}, Nested)
        messages = post.call_args.kwargs['json']['messages']
        feedback = json.loads(messages[2]['content'])['validation_feedback']
        self.assertEqual(feedback, [{'type': 'extra_forbidden', 'path': ['items', 0, '*']}])
        self.assertNotIn('private-key', messages[2]['content'])
        self.assertNotIn('secret', messages[2]['content'])
        self.assertIn('零基序号', messages[0]['content'])

    def test_invalid_response_retries_only_once_and_http_failure_never_retries(self):
        from src.agents.literature import Route
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        invalid = httpx.Response(200, request=request, json={'choices': [{'message': {'content': '{}'}}]})
        failure = httpx.Response(503, request=request, text='private body')
        with patch('src.integrations.model.settings.model_config', return_value=config):
            with patch('src.integrations.model.httpx.post', return_value=invalid) as post:
                with self.assertRaisesRegex(StructuredOutputError, '结构化结果无效'):
                    structured('runtime/route', {}, Route)
                self.assertEqual(post.call_count, 2)
            with patch('src.integrations.model.httpx.post', return_value=failure) as post:
                with self.assertRaisesRegex(ModelServiceError, r'模型服务请求失败 \(HTTP 503\)') as error:
                    structured('runtime/route', {}, Route)
                self.assertNotIsInstance(error.exception, StructuredOutputError)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(self.diagnostics_mock.call_args.kwargs['kind'], 'http_error')
                self.assertNotIn('response', self.diagnostics_mock.call_args.kwargs)
            with patch('src.integrations.model.httpx.post', side_effect=httpx.ConnectError('private body')) as post:
                with self.assertRaisesRegex(ModelServiceError, '模型服务连接失败或超时，请检查设置'):
                    structured('runtime/route', {}, Route)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(self.diagnostics_mock.call_args.kwargs['kind'], 'network_error')
                self.assertNotIn('response', self.diagnostics_mock.call_args.kwargs)

    def test_corrected_validation_failure_writes_replayable_diagnostic(self):
        from src.agents.literature import Route
        from src.infrastructure.diagnostics import diagnostic_scope
        from scripts.replay_diagnostic import replay
        self.diagnostics.stop()
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        responses = [httpx.Response(200, request=request, json={'choices': [{'message': {'content': value}}]}) for value in
                     ('{"action":"bad"}', '{"action":"direct","answer":"ok"}')]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}), patch('src.infrastructure.settings._local', return_value={}), patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'private-key'}), patch('src.integrations.model.httpx.post', side_effect=responses):
            with diagnostic_scope(run_id=42):
                result = structured('runtime/route', {'question': 'hello'}, Route)
            self.assertEqual(result.answer, 'ok')
            files = list((Path(directory) / 'diagnostics').glob('*.json'))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding='utf-8')
            self.assertNotIn('private-key', text)
            artifact = json.loads(text)
            self.assertEqual(artifact['context']['run_id'], 42)
            self.assertEqual(artifact['context']['prompt_id'], 'runtime/route')
            self.assertEqual(artifact['payload'], {'question': 'hello'})
            self.assertTrue(replay(artifact).startswith('复现'))

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
