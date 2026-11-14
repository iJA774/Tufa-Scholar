import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from jinja2 import StrictUndefined, UndefinedError
from pydantic import BaseModel, ConfigDict

import src.integrations.model as model_client
from src.integrations.model import PROMPT_ENV, PROMPT_IDS, ModelServiceError, StructuredOutputError, structured, structured_input_units


class Output(BaseModel):
    answer: str


class ModelTests(unittest.TestCase):
    def setUp(self):
        model_client.UNSUPPORTED_REQUEST_PARAMS.clear()
        self.diagnostics = patch('src.integrations.model.record_failure', return_value='diagnostic-1')
        self.diagnostics_mock = self.diagnostics.start()
        self.addCleanup(self.diagnostics.stop)

    @staticmethod
    def context(role='test node', mode='none', tools=None, local_papers=None, research_mode='auto'):
        return {
            'node_role': role,
            'search_mode': mode,
            'research_mode': research_mode,
            'available_tools': tools or [],
            'local_papers': local_papers or [],
        }

    @staticmethod
    def response(content='{"answer":"ok"}', status=200):
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        if status != 200:
            return httpx.Response(status, request=request, text=content)
        return httpx.Response(200, request=request, json={'choices': [{'message': {'content': content}}]})

    def test_all_real_prompt_ids_render_time_and_schema_at_http_boundary(self):
        cases = [
            ('runtime/dispatcher', '分流角色', 'none', []),
            ('literature/background_investigator', '背景调查角色', 'none', [{'name': 'search_web', 'description': '网页检索', 'input_schema': {}}]),
            ('literature/planner', '计划角色', 'paper', [{'name': 'search_papers', 'description': '论文检索', 'input_schema': {}}]),
            ('literature/researcher', '论文研究角色', 'paper', [{'name': 'collect_evidence', 'description': '提取证据', 'input_schema': {}}]),
            ('literature/analyst', '分析角色', 'web', []),
            ('literature/reporter', '报告角色', 'web', []),
        ]
        self.assertEqual({case[0] for case in cases}, PROMPT_IDS)
        self.assertIs(PROMPT_ENV.undefined, StrictUndefined)
        self.assertIs(PROMPT_ENV.autoescape, False)
        output = {'answer': 'ok'}
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        payload = {'question': '材料 {{ CURRENT_TIME }} {{ OUTPUT_SCHEMA }} {% include "private" %}'}
        clock_values = [
            datetime(2026, 12, 31, 23, 59, second, tzinfo=timezone(timedelta(hours=8)))
            for second in range(54, 60)
        ]
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.datetime') as clock:
            clock.now.side_effect = clock_values
            for index, (prompt_id, role, mode, tools) in enumerate(cases):
                with self.subTest(prompt_id=prompt_id), patch('src.integrations.model.httpx.post', return_value=self.response(json.dumps(output))) as post:
                    result = structured(prompt_id, payload, Output, template_context=self.context(role, mode, tools))
                self.assertEqual(result.model_dump(), output)
                body = post.call_args.kwargs['json']
                system, user = body['messages']
                expected_time = clock_values[index].isoformat(timespec='seconds')
                self.assertTrue(system['content'].startswith(f'---\nCURRENT_TIME: {expected_time}\n---\n'))
                self.assertIn(role, system['content'])
                self.assertIn(f'`{mode}`', system['content'])
                for tool in tools:
                    self.assertIn(tool['name'], system['content'])
                    self.assertIn(tool['description'], system['content'])
                self.assertIn(json.dumps(Output.model_json_schema(), ensure_ascii=False, separators=(',', ':')), system['content'])
                self.assertNotIn('{{ CURRENT_TIME }}', system['content'])
                self.assertNotIn('{{ OUTPUT_SCHEMA }}', system['content'])
                self.assertEqual(user['role'], 'user')
                self.assertEqual(json.loads(user['content']), payload)
                self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(clock.now.call_count, len(cases))

    def test_structured_input_units_measure_rendered_system_and_serialized_payload_deterministically(self):
        context = self.context('planner role', 'paper', [{'name': 'search_papers', 'description': 'search', 'input_schema': {}}])
        first_payload = {'question': '中文 {{ CURRENT_TIME }}', 'phase': 'query'}
        units = structured_input_units('literature/planner', first_payload, Output, template_context=context)
        self.assertIs(type(units), int)
        self.assertGreater(units, len(json.dumps(first_payload, ensure_ascii=False)))
        self.assertEqual(
            units,
            structured_input_units('literature/planner', first_payload, Output, template_context=context),
        )
        self.assertGreater(
            structured_input_units('literature/planner', {**first_payload, 'evidence': ['更多证据'] * 20}, Output, template_context=context),
            units,
        )

    def test_decision_stages_use_low_effort_and_4096_output_cap_but_writing_stages_are_uncapped(self):
        config = {'base_url': 'https://decision-options.example/v1', 'model': 'test'}
        with patch('src.integrations.model.settings.model_config', return_value=config):
            for prompt_id, payload in (
                ('literature/background_investigator', {'phase': 'query'}),
                ('literature/planner', {}),
                ('literature/analyst', {}),
                ('literature/researcher', {'phase': 'act'}),
            ):
                with self.subTest(prompt_id=prompt_id), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
                    structured(prompt_id, payload, Output, template_context=self.context())
                request = post.call_args.kwargs['json']
                self.assertEqual(request['reasoning_effort'], 'low')
                self.assertEqual(request['max_completion_tokens'], 4096)

            for prompt_id, payload in (
                ('runtime/dispatcher', {'phase': 'query'}),
                ('literature/researcher', {'phase': 'draft'}),
                ('literature/reporter', {}),
            ):
                with self.subTest(prompt_id=prompt_id), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
                    structured(prompt_id, payload, Output, template_context=self.context())
                request = post.call_args.kwargs['json']
                self.assertNotIn('reasoning_effort', request)
                self.assertNotIn('max_completion_tokens', request)

    def test_explicit_effort_overrides_default_and_higher_effort_has_no_output_cap(self):
        config = {'base_url': 'https://explicit-effort.example/v1', 'model': 'test'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
            structured('literature/planner', {}, Output,
                       model_options={'reasoning_effort': 'high'}, template_context=self.context())
        request = post.call_args.kwargs['json']
        self.assertEqual(request['reasoning_effort'], 'high')
        self.assertNotIn('max_completion_tokens', request)

    def test_optional_decision_parameters_retry_only_after_explicit_rejection_and_cache_support(self):
        config = {'base_url': 'https://option-compat.example/v1', 'model': 'test'}
        request = httpx.Request('POST', config['base_url'] + '/chat/completions')
        rejected = httpx.Response(
            400,
            request=request,
            json={'error': {'message': 'Unsupported parameter: max_completion_tokens', 'param': 'max_completion_tokens'}},
        )
        sent = []

        def post(url, **kwargs):
            sent.append(dict(kwargs['json']))
            return rejected if len(sent) == 1 else self.response()

        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', side_effect=post):
            structured('literature/planner', {}, Output, template_context=self.context())
            structured('literature/planner', {}, Output, template_context=self.context())

        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0]['max_completion_tokens'], 4096)
        self.assertNotIn('max_completion_tokens', sent[1])
        self.assertNotIn('max_completion_tokens', sent[2])
        self.assertTrue(all(request['reasoning_effort'] == 'low' for request in sent))
        self.assertLessEqual(len(model_client.UNSUPPORTED_REQUEST_PARAMS), model_client.MAX_UNSUPPORTED_REQUEST_PARAMS)

    def test_explicit_unsupported_user_effort_is_not_silently_dropped(self):
        config = {'base_url': 'https://explicit-unsupported.example/v1', 'model': 'test'}
        request = httpx.Request('POST', config['base_url'] + '/chat/completions')
        rejected = httpx.Response(
            400,
            request=request,
            json={'error': {'message': 'Unsupported parameter: reasoning_effort', 'param': 'reasoning_effort'}},
        )
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=rejected) as post:
            with self.assertRaises(ModelServiceError):
                structured('literature/planner', {}, Output,
                           model_options={'reasoning_effort': 'high'}, template_context=self.context())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['json']['reasoning_effort'], 'high')
        self.assertNotIn('max_completion_tokens', post.call_args.kwargs['json'])

    def test_invalid_parameter_value_does_not_trigger_compatibility_retry(self):
        config = {'base_url': 'https://invalid-value.example/v1', 'model': 'test'}
        request = httpx.Request('POST', config['base_url'] + '/chat/completions')
        rejected = httpx.Response(
            400,
            request=request,
            json={'error': {'message': 'Invalid value for max_completion_tokens', 'param': 'max_completion_tokens'}},
        )
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=rejected) as post:
            with self.assertRaises(ModelServiceError):
                structured('literature/planner', {}, Output, template_context=self.context())
        self.assertEqual(post.call_count, 1)

    def test_rejected_default_effort_value_falls_back_without_disabling_other_effort_levels(self):
        config = {'base_url': 'https://effort-value.example/v1', 'model': 'test'}
        request = httpx.Request('POST', config['base_url'] + '/chat/completions')
        rejected = httpx.Response(
            400,
            request=request,
            json={'error': {'message': 'Invalid value for reasoning_effort: low is not supported', 'param': 'reasoning_effort'}},
        )
        sent = []

        def post(url, **kwargs):
            sent.append(dict(kwargs['json']))
            return rejected if len(sent) == 1 else self.response()

        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', side_effect=post):
            structured('literature/planner', {}, Output, template_context=self.context())
            structured('literature/planner', {}, Output, template_context=self.context())
            structured('literature/planner', {}, Output,
                       model_options={'reasoning_effort': 'high'}, template_context=self.context())

        self.assertEqual(len(sent), 4)
        self.assertEqual(sent[0]['reasoning_effort'], 'low')
        self.assertNotIn('reasoning_effort', sent[1])
        self.assertNotIn('reasoning_effort', sent[2])
        self.assertEqual(sent[3]['reasoning_effort'], 'high')
        self.assertTrue(model_client._parameter_is_known_unsupported(config, 'reasoning_effort=low'))
        self.assertFalse(model_client._parameter_is_known_unsupported(config, 'reasoning_effort'))

    def test_output_cap_length_response_is_recorded_without_same_cap_retry_for_json_and_stream(self):
        config = {'base_url': 'https://cap-length.example/v1', 'model': 'test'}
        request = httpx.Request('POST', config['base_url'] + '/chat/completions')
        usage = {'prompt_tokens': 100, 'completion_tokens': 4096, 'total_tokens': 4196}
        ordinary = httpx.Response(
            200,
            request=request,
            json={'choices': [{'message': {'content': '{"answer":"partial'}, 'finish_reason': 'length'}], 'usage': usage},
        )
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=ordinary) as post, self.assertLogs('src.integrations.model', level='INFO') as logs:
            with self.assertRaises(StructuredOutputError):
                structured('literature/planner', {}, Output, template_context=self.context())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.diagnostics_mock.call_args.kwargs['response'], '{"answer":"partial')
        self.assertEqual('\n'.join(logs.output).count('completion_tokens=4096'), 1)

        events = [
            {'choices': [{'delta': {'content': '{'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'length'}], 'usage': usage},
        ]
        encoded = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
        streamed = httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'}, content=encoded)
        observed = []
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.stream', return_value=nullcontext(streamed)) as stream:
            with self.assertRaises(StructuredOutputError):
                structured('literature/planner', {}, Output, template_context=self.context(),
                           on_stream=lambda kind, data: observed.append((kind, data)))
        self.assertEqual(stream.call_count, 1)
        self.assertEqual([kind for kind, _ in observed], ['model.started', 'model.delta', 'model.failed'])

    def test_valid_json_is_accepted_even_when_provider_marks_length_finish_reason(self):
        config = {'base_url': 'https://valid-length.example/v1', 'model': 'test'}
        response = httpx.Response(
            200,
            request=httpx.Request('POST', config['base_url'] + '/chat/completions'),
            json={'choices': [{'message': {'content': '{"answer":"ok"}'}, 'finish_reason': 'length'}]},
        )
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=response) as post:
            result = structured('literature/planner', {}, Output, template_context=self.context())
        self.assertEqual(result.answer, 'ok')
        self.assertEqual(post.call_count, 1)

    def test_reporter_repairs_only_legacy_section_limitations_and_validates_merged_data(self):
        from src.agents.literature import ReporterOutput

        output = {
            'sections': [{
                'heading': '研究结果',
                'paragraphs': [{'text': '已有结论。', 'citations': ['E1']}],
                'limitations': [{'text': '章节局限。', 'citations': []}],
            }],
            'limitations': [{'text': '报告局限。', 'citations': []}],
        }
        config = {'base_url': 'https://report-repair.example/v1', 'model': 'test'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=self.response(json.dumps(output))) as post:
            result = structured('literature/reporter', {'evidence': [{'id': 'E1'}]}, ReporterOutput,
                                template_context=self.context())

        self.assertEqual(post.call_count, 1)
        self.assertEqual([item.text for item in result.limitations], ['报告局限。', '章节局限。'])
        self.assertEqual(result.sections[0].paragraphs[0].citations, ['E1'])

    def test_planner_gets_local_filenames_tools_and_role_without_rendering_them_again(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        context = self.context(
            '科研计划制定者', 'paper',
            [{'name': 'search_papers', 'description': '只检索允许范围内的论文', 'input_schema': {'type': 'object'}}],
            ['<研究图表>.pdf {{ CURRENT_TIME }}'],
        )
        context['CURRENT_TIME'] = 'forged time'
        context['OUTPUT_SCHEMA'] = 'forged schema'
        payload = {'question': 'private question {{ CURRENT_TIME }}', 'history': ['{{ OUTPUT_SCHEMA }}']}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
            structured('literature/planner', payload, Output, template_context=context)
        system, user = post.call_args.kwargs['json']['messages']
        self.assertIn('科研计划制定者', system['content'])
        self.assertIn('paper', system['content'])
        self.assertIn('论文规划身份', system['content'])
        self.assertIn('search_papers', system['content'])
        self.assertIn('只检索允许范围内的论文', system['content'])
        self.assertIn('<研究图表>.pdf {{ CURRENT_TIME }}', system['content'])
        self.assertNotIn('forged time', system['content'])
        self.assertNotIn('forged schema', system['content'])
        self.assertNotIn('private question', system['content'])
        self.assertEqual(json.loads(user['content']), payload)
        self.assertIn('{{ CURRENT_TIME }}', user['content'])
        self.assertIn('{{ OUTPUT_SCHEMA }}', user['content'])

    def test_web_and_paper_researcher_identity_is_selected_by_search_mode(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        modes = [('web', '网页研究身份'), ('paper', '论文研究身份')]
        with patch('src.integrations.model.settings.model_config', return_value=config):
            for mode, identity in modes:
                with self.subTest(mode=mode), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
                    structured('literature/researcher', {}, Output, template_context=self.context('专业研究者', mode))
                system = post.call_args.kwargs['json']['messages'][0]['content']
                self.assertIn(identity, system)

    def test_time_and_schema_cannot_be_overridden_and_retry_gets_fresh_time(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        times = [
            datetime(2027, 1, 1, 0, 0, 1, tzinfo=timezone(timedelta(hours=8))),
            datetime(2027, 1, 1, 0, 0, 2, tzinfo=timezone(timedelta(hours=8))),
        ]
        sent = []

        def post(url, **kwargs):
            sent.append(kwargs['json'])
            content = '{broken' if len(sent) == 1 else '{"answer":"ok"}'
            return self.response(content)

        context = self.context('直答分流者')
        context.update({'CURRENT_TIME': 'forged', 'OUTPUT_SCHEMA': 'forged'})
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.datetime') as clock, patch('src.integrations.model.httpx.post', side_effect=post):
            clock.now.side_effect = times
            result = structured('runtime/dispatcher', {'question': 'hello'}, Output, template_context=context)
        self.assertEqual(result.answer, 'ok')
        first = sent[0]['messages'][0]['content']
        second = sent[1]['messages'][0]['content']
        self.assertIn(times[0].isoformat(timespec='seconds'), first)
        self.assertIn(times[1].isoformat(timespec='seconds'), second)
        self.assertNotIn('forged', first + second)
        self.assertIn('validation_feedback', sent[1]['messages'][2]['content'])

    def test_undefined_template_variable_fails_before_http_request(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        undefined_template = PROMPT_ENV.from_string('{{ missing_template_value }}')
        with patch('src.integrations.model.settings.model_config', return_value=config), patch.object(PROMPT_ENV, 'get_template', return_value=undefined_template), patch('src.integrations.model.httpx.post') as post:
            with self.assertRaises(UndefinedError):
                structured('runtime/dispatcher', {}, Output, template_context=self.context())
        post.assert_not_called()

    def test_unknown_prompt_id_is_rejected_without_http(self):
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), patch('src.integrations.model.httpx.post') as post:
            with self.assertRaisesRegex(ValueError, '未知模型提示词 ID'):
                structured('runtime/not-a-node', {}, Output)
        post.assert_not_called()

    def test_one_safe_schema_correction_preserves_role_context_and_options(self):
        class StrictOutput(BaseModel):
            model_config = ConfigDict(extra='forbid')
            answer: str

        config = {'base_url': 'https://example.invalid/v1', 'model': 'default', 'api_key': 'secret-key'}
        sent = []

        def post(url, **kwargs):
            sent.append(kwargs['json'])
            content = '{"answer":"ok","private-field-name":"private-value"}' if len(sent) == 1 else '{"answer":"ok"}'
            return self.response(content)

        context = self.context('dispatcher', 'none')
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', side_effect=post), self.assertLogs('src.integrations.model', level='INFO') as logs:
            result = structured('runtime/dispatcher', {'question': 'private question'}, StrictOutput,
                                model_options={'model_name': 'chosen', 'reasoning_effort': 'high'}, template_context=context)
        self.assertEqual(result.answer, 'ok')
        diagnostic = self.diagnostics_mock.call_args.kwargs
        self.assertEqual(diagnostic['response'], '{"answer":"ok","private-field-name":"private-value"}')
        self.assertEqual(diagnostic['secrets'], ('secret-key',))
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(sent[0]['messages']), 2)
        first_prompt_body = sent[0]['messages'][0]['content'].split('\n---\n', 1)[1]
        second_prompt_body = sent[1]['messages'][0]['content'].split('\n---\n', 1)[1]
        self.assertEqual(first_prompt_body, second_prompt_body)
        self.assertEqual(json.loads(sent[1]['messages'][2]['content'])['validation_feedback'], [{'type': 'extra_forbidden', 'path': ['*']}])
        for request in sent:
            self.assertEqual((request['model'], request['reasoning_effort']), ('chosen', 'high'))
        feedback_and_logs = sent[1]['messages'][2]['content'] + ' '.join(logs.output)
        for secret in ('secret-key', 'private-field-name', 'private-value', 'private question'):
            self.assertNotIn(secret, feedback_and_logs)

    def test_nested_array_index_survives_safe_feedback(self):
        class Item(BaseModel):
            model_config = ConfigDict(extra='forbid')
            value: str

        class Nested(BaseModel):
            items: list[Item]

        sent = []

        def post(url, **kwargs):
            sent.append(kwargs['json'])
            content = '{"items":[{"value":"ok","private-key":"secret"}]}' if len(sent) == 1 else '{"items":[{"value":"ok"}]}'
            return self.response(content)

        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), patch('src.integrations.model.httpx.post', side_effect=post):
            structured('runtime/dispatcher', {}, Nested, template_context=self.context())
        messages = sent[1]['messages']
        feedback = json.loads(messages[2]['content'])['validation_feedback']
        self.assertEqual(feedback, [{'type': 'extra_forbidden', 'path': ['items', 0, '*']}])
        self.assertNotIn('private-key', messages[2]['content'])
        self.assertNotIn('secret', messages[2]['content'])
        self.assertIn('零基序号', messages[0]['content'])

    def test_invalid_response_retries_only_once_and_http_failure_never_retries(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        with patch('src.integrations.model.settings.model_config', return_value=config):
            with patch('src.integrations.model.httpx.post', return_value=self.response('{}')) as post:
                with self.assertRaisesRegex(StructuredOutputError, '结构化结果无效'):
                    structured('runtime/dispatcher', {}, Output, template_context=self.context())
                self.assertEqual(post.call_count, 2)
            with patch('src.integrations.model.httpx.post', return_value=self.response('private body', status=503)) as post:
                with self.assertRaisesRegex(ModelServiceError, r'模型服务请求失败 \(HTTP 503\)') as error:
                    structured('runtime/dispatcher', {}, Output, template_context=self.context())
                self.assertNotIsInstance(error.exception, StructuredOutputError)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(self.diagnostics_mock.call_args.kwargs['kind'], 'http_error')
                self.assertNotIn('response', self.diagnostics_mock.call_args.kwargs)

    def test_corrected_validation_failure_writes_replayable_diagnostic(self):
        from src.agents.literature import DispatcherOutput
        from src.infrastructure.diagnostics import diagnostic_scope
        from scripts.replay_diagnostic import replay

        self.diagnostics.stop()
        invalid = '{"needs_research":"invalid-boolean","reason":"simple","answer":"hello"}'
        valid = '{"needs_research":false,"reason":"simple","answer":"ok"}'
        responses = [invalid, valid]

        def post(url, **kwargs):
            return self.response(responses.pop(0))

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}), patch('src.infrastructure.settings._local', return_value={}), patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'private-key'}), patch('src.integrations.model.httpx.post', side_effect=post):
            with diagnostic_scope(run_id=42):
                result = structured('runtime/dispatcher', {'question': 'hello'}, DispatcherOutput, template_context=self.context())
            self.assertEqual(result.answer, 'ok')
            files = list((Path(directory) / 'diagnostics').glob('*.json'))
            self.assertEqual(len(files), 1)
            artifact_text = files[0].read_text(encoding='utf-8')
            self.assertNotIn('private-key', artifact_text)
            artifact = json.loads(artifact_text)
            self.assertEqual(artifact['context']['run_id'], 42)
            self.assertEqual(artifact['context']['prompt_id'], 'runtime/dispatcher')
            self.assertEqual(artifact['payload'], {'question': 'hello'})
            self.assertTrue(replay(artifact).startswith('复现'))

    def test_template_loads_outside_repository_root_and_optional_context_has_safe_defaults(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        original_cwd = Path.cwd()
        try:
            os.chdir(Path(__file__).resolve().parent)
            with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
                self.assertEqual(structured('runtime/dispatcher', {'question': 'test'}, Output).answer, 'ok')
            system = post.call_args.kwargs['json']['messages'][0]['content']
            self.assertTrue(system.startswith('---\nCURRENT_TIME: '))
            self.assertIn('（无可调用工具）', system)
        finally:
            os.chdir(original_cwd)

    def test_dispatcher_prompt_uses_trusted_research_mode_context(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
            structured(
                'runtime/dispatcher', {'question': 'safe payload'}, Output,
                template_context=self.context(research_mode='quick'),
            )
        system = post.call_args.kwargs['json']['messages'][0]['content']
        user = post.call_args.kwargs['json']['messages'][1]['content']
        self.assertIn('当前是快速探索模式', system)
        self.assertIn('answer', system)
        self.assertEqual(json.loads(user), {'question': 'safe payload'})

    def test_missing_config_never_calls_http(self):
        with patch('src.integrations.model.settings.model_config', return_value={}), patch('src.integrations.model.httpx.post') as post:
            with self.assertRaisesRegex(RuntimeError, '设置'):
                structured('runtime/dispatcher', {}, Output)
            post.assert_not_called()

    def test_http_error_does_not_expose_key_or_body(self):
        response = self.response('secret-key', status=401)
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'secret-key'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=response), self.assertLogs('src.integrations.model', level='INFO') as logs:
            with self.assertRaises(RuntimeError) as error:
                structured('runtime/dispatcher', {}, Output)
        self.assertNotIn('secret-key', str(error.exception))
        self.assertNotIn('secret-key', ' '.join(logs.output))
        self.assertIn('elapsed_ms=', ' '.join(logs.output))
        self.assertIn('401', str(error.exception))

    def test_model_usage_is_logged_with_task_and_prompt_without_sensitive_text(self):
        response = self.response()
        body = response.json()
        body['usage'] = {
            'prompt_tokens': 120,
            'completion_tokens': 24,
            'total_tokens': 144,
            'prompt_tokens_details': {'cached_tokens': 18},
            'completion_tokens_details': {'reasoning_tokens': 7},
        }
        response = httpx.Response(200, request=response.request, json=body)
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
        with patch('src.integrations.model.settings.model_config', return_value=config), \
             patch('src.integrations.model.httpx.post', return_value=response), \
             patch('src.integrations.model.diagnostic_context', return_value={'run_id': 42}), \
             self.assertLogs('src.integrations.model', level='INFO') as logs:
            structured('runtime/dispatcher', {'question': 'private research question'}, Output,
                       template_context=self.context())

        output = ' '.join(logs.output)
        self.assertIn('run_id=42', output)
        self.assertIn('prompt_id=runtime/dispatcher', output)
        self.assertIn('attempt=1', output)
        self.assertIn('prompt_tokens=120', output)
        self.assertIn('completion_tokens=24', output)
        self.assertIn('total_tokens=144', output)
        self.assertIn('cached_tokens=18', output)
        self.assertIn('reasoning_tokens=7', output)
        self.assertIn('input_chars=', output)
        self.assertNotIn('private research question', output)

    def test_invalid_structured_response_logs_usage_for_each_response_attempt(self):
        response = httpx.Response(
            200,
            request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'),
            json={
                'choices': [{'message': {'content': '{}'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105},
            },
        )
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), \
             patch('src.integrations.model.httpx.post', return_value=response) as post, \
             patch('src.integrations.model.diagnostic_context', return_value={'run_id': 9}), \
             self.assertLogs('src.integrations.model', level='INFO') as logs:
            with self.assertRaises(StructuredOutputError):
                structured('runtime/dispatcher', {'question': 'test'}, Output,
                           template_context=self.context())

        output = '\n'.join(logs.output)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(output.count('model_call invalid'), 2)
        self.assertEqual(output.count('run_id=9'), 4)
        self.assertEqual(output.count('prompt_tokens=100'), 2)
        self.assertEqual(output.count('completion_tokens=5'), 2)
        self.assertEqual(output.count('total_tokens=105'), 2)

    def test_streaming_reads_only_sse_content_and_coalesces_small_chunks(self):
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        events = [
            {'choices': [{'delta': {'reasoning_content': 'private reasoning', 'content': '{"ans'}}]},
            {'choices': [{'delta': {'reasoning_content': 'more private reasoning', 'content': 'wer":"'}}]},
            {'choices': [{'delta': {'content': 'ok"}'}}], 'usage': {'prompt_tokens': 11, 'completion_tokens': 4, 'total_tokens': 15}},
        ]
        payload = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
        response = httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'}, content=payload)
        observed = []
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'private-key'}
        with patch('src.integrations.model.settings.model_config', return_value=config), \
             patch('src.integrations.model.httpx.stream', return_value=nullcontext(response)) as stream, \
             self.assertLogs('src.integrations.model', level='INFO') as logs:
            result = structured('literature/researcher', {'phase': 'act', 'question': 'private prompt'}, Output,
                                on_stream=lambda kind, data: observed.append((kind, data)))

        self.assertEqual(result.answer, 'ok')
        body = stream.call_args.kwargs['json']
        self.assertTrue(body['stream'])
        self.assertNotIn('stream_options', body)
        self.assertEqual([kind for kind, _ in observed], ['model.started', 'model.delta', 'model.completed'])
        self.assertEqual(''.join(data['text'] for kind, data in observed if kind == 'model.delta'), '{"answer":"ok"}')
        self.assertTrue(all(data['attempt'] == 1 and data['phase'] == 'act' for _, data in observed))
        public_events = json.dumps(observed, ensure_ascii=False)
        self.assertNotIn('private reasoning', public_events)
        self.assertNotIn('private prompt', public_events)
        self.assertIn('prompt_tokens=11', ' '.join(logs.output))

    def test_stream_mode_accepts_ordinary_json_when_provider_ignores_stream_flag(self):
        response = httpx.Response(
            200,
            request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'),
            headers={'content-type': 'application/json'},
            stream=httpx.ByteStream(json.dumps({'choices': [{'message': {'content': '{"answer":"ok"}'}}]}).encode()),
        )
        observed = []
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), \
             patch('src.integrations.model.httpx.stream', return_value=nullcontext(response)) as stream:
            result = structured('runtime/dispatcher', {'phase': 'query'}, Output,
                                on_stream=lambda kind, data: observed.append((kind, data)))

        self.assertEqual(result.answer, 'ok')
        self.assertEqual([kind for kind, _ in observed], ['model.started', 'model.delta', 'model.completed'])
        self.assertEqual(observed[1][1]['text'], '{"answer":"ok"}')
        self.assertTrue(stream.call_args.kwargs['json']['stream'])

    def test_stream_mode_retries_without_stream_only_after_explicit_parameter_rejection(self):
        rejected = httpx.Response(
            400,
            request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'),
            headers={'content-type': 'application/json'},
            stream=httpx.ByteStream(json.dumps({'error': {'message': 'Unsupported parameter: stream', 'param': 'stream'}}).encode()),
        )
        observed = []
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), \
             patch('src.integrations.model.httpx.stream', return_value=nullcontext(rejected)) as stream, \
             patch('src.integrations.model.httpx.post', return_value=self.response()) as post:
            result = structured('runtime/dispatcher', {}, Output,
                                on_stream=lambda kind, data: observed.append((kind, data)))
        self.assertEqual(result.answer, 'ok')
        self.assertEqual(stream.call_count, 1)
        self.assertNotIn('stream', post.call_args.kwargs['json'])
        self.assertEqual([kind for kind, _ in observed], ['model.started', 'model.delta', 'model.completed'])

    def test_streaming_redacts_known_key_even_when_split_across_sse_chunks(self):
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        secret = 'the-secret-key'
        content = '{"answer":"12345678901234567890' + secret + '"}'
        chunks = [content[:40], content[40:]]
        encoded = ''.join(
            'data: ' + json.dumps({'choices': [{'delta': {'content': chunk}}]}) + '\n\n'
            for chunk in chunks
        ) + 'data: [DONE]\n\n'
        response = httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'}, content=encoded)
        observed = []
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': secret}
        with patch('src.integrations.model.settings.model_config', return_value=config), \
             patch('src.integrations.model.httpx.stream', return_value=nullcontext(response)):
            result = structured('runtime/dispatcher', {}, Output,
                                on_stream=lambda kind, data: observed.append((kind, data)))
        self.assertIn(secret, result.answer)
        emitted = ''.join(data['text'] for kind, data in observed if kind == 'model.delta')
        self.assertNotIn(secret, emitted)
        self.assertIn('[REDACTED]', emitted)

    def test_stream_validation_retry_has_distinct_attempt_and_transport_failure_is_not_retried(self):
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')

        def sse(content):
            body = 'data: ' + json.dumps({'choices': [{'delta': {'content': content}}]}) + '\n\ndata: [DONE]\n\n'
            return httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'}, content=body)

        responses = [sse('{}'), sse('{"answer":"ok"}')]
        observed = []
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), \
             patch('src.integrations.model.httpx.stream', side_effect=lambda *args, **kwargs: nullcontext(responses.pop(0))) as stream:
            result = structured('runtime/dispatcher', {}, Output,
                                on_stream=lambda kind, data: observed.append((kind, data)))
        self.assertEqual(result.answer, 'ok')
        self.assertEqual(stream.call_count, 2)
        self.assertEqual([data['attempt'] for kind, data in observed if kind == 'model.started'], [1, 2])
        self.assertEqual([data['attempt'] for kind, data in observed if kind == 'model.completed'], [2])
        self.assertEqual([data['attempt'] for kind, data in observed if kind == 'model.failed'], [1])

        partial = unittest.mock.Mock()
        partial.headers = {'content-type': 'text/event-stream'}
        partial.status_code = 200
        partial.raise_for_status.return_value = None

        def interrupted_lines():
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': '{"answer"'}}]})
            raise httpx.ReadTimeout('private timeout detail')

        partial.iter_lines.return_value = interrupted_lines()
        failed_events = []
        with patch('src.integrations.model.settings.model_config', return_value={'base_url': 'https://example.invalid/v1', 'model': 'test'}), \
             patch('src.integrations.model.httpx.stream', return_value=nullcontext(partial)) as stream:
            with self.assertRaises(ModelServiceError):
                structured('runtime/dispatcher', {}, Output,
                           on_stream=lambda kind, data: failed_events.append((kind, data)))
        self.assertEqual(stream.call_count, 1)
        self.assertEqual([kind for kind, _ in failed_events], ['model.started', 'model.delta', 'model.failed'])
        self.assertNotIn('private timeout detail', json.dumps(failed_events))


if __name__ == '__main__':
    unittest.main()
