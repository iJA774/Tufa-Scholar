import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agents import literature as workflow
from src.integrations.model import ModelServiceError, StructuredOutputError


def researcher_output(*, action="finish", tool_name="", arguments=None, reason="", draft=None, findings=None):
    """Return a lightweight test response independent of either phase schema."""
    return SimpleNamespace(
        action=action,
        tool_name=tool_name,
        arguments=arguments or {},
        reason=reason,
        draft=draft or workflow.Draft(),
        findings=findings or [],
    )


def paper(identifier, title, abstract, doi=None):
    return {
        'id': identifier,
        'title': title,
        'abstract': abstract,
        'doi': doi or f'10.1234/{identifier}',
        'source_level': 'abstract',
        'versions': [],
    }


def page(title='Official results', url='https://example.org/accuracy', content='Accuracy was 92%.'):
    return {
        'title': title,
        'url': url,
        'content': content,
        'source_level': 'web_snippet',
        'provider': 'fixture',
        'retrieved_at': '2026-11-12T10:00:00+08:00',
    }


class ModelFixture:
    def __init__(self, *, initial_source='paper', next_source=None, rounds=1,
                 sufficient=None, direct=False, selected_title=None, reporter_citations=None,
                 planner_first_source=None):
        self.initial_source = initial_source
        self.next_source = next_source or initial_source
        self.round_count = rounds
        self.sufficient = sufficient or [True, True]
        self.direct = direct
        self.selected_title = selected_title
        self.reporter_citations = reporter_citations
        self.planner_first_source = planner_first_source
        self.calls = []
        self.reporter_payloads = []
        self.researcher_payloads = []
        self.planner_payloads = []
        self.analyst_contexts = []

    def __call__(self, prompt_id, payload, schema, **kwargs):
        self.calls.append((prompt_id, payload, kwargs))
        if prompt_id == 'runtime/dispatcher':
            if self.direct:
                return workflow.DispatcherOutput(needs_research=False, reason='无需深研', answer='直接回答 fixture。')
            return workflow.DispatcherOutput(needs_research=True, reason='需要证据', answer='')

        if prompt_id == 'literature/background_investigator':
            if payload['phase'] == 'query':
                source = self.initial_source
                return workflow.BackgroundOutput(source=source, queries=[payload['question']])
            candidates = payload.get('candidates', [])
            if self.selected_title:
                chosen = next((item for item in candidates if item.get('title') == self.selected_title), None)
            else:
                chosen = candidates[0] if candidates else None
            return workflow.BackgroundOutput(
                source=payload.get('search_mode', self.initial_source),
                selected_candidate_ids=[chosen['candidate_id']] if chosen else [],
                summary='已检查真实搜索候选。' if chosen else '没有检索到候选。',
                gaps=[] if chosen else ['没有候选资料'],
            )

        if prompt_id == 'literature/planner':
            self.planner_payloads.append(payload)
            round_count = payload['round_count']
            selected_source = (self.planner_first_source or self.initial_source) if round_count == 0 else self.next_source
            action = 'research' if round_count < self.round_count else 'report'
            # The two round test deliberately asks to continue after round two.
            if round_count >= 2:
                action = 'research'
            return workflow.PlannerOutput(
                requirements=['准确率及证据来源'],
                plan=['检索 accuracy 并收集支持材料'],
                source=selected_source,
                action=action,
                rationale='fixture plan',
            )

        if prompt_id == 'literature/researcher':
            self.researcher_payloads.append(payload)
            mode = payload['current_source']
            tools = {item['name'] for item in kwargs['template_context']['available_tools']}
            candidates = payload.get('current_candidates', [])
            evidence = payload.get('evidence', [])
            mode_evidence = [
                item for item in evidence
                if (item.get('source_level') in {'web_content', 'web_snippet'} if mode == 'web'
                    else item.get('source_level') not in {'web_content', 'web_snippet'})
            ]
            selected_ids = payload.get('selected_candidate_ids', [])
            selected = next((item for item in candidates if item.get('candidate_id') in selected_ids), None)
            candidate = selected or (candidates[0] if candidates else None)
            if not candidates:
                tool = 'search_web' if mode == 'web' else 'search_papers'
                if tool in tools:
                    arguments = {'query': 'accuracy', 'limit': 5} if mode == 'web' else {'query': 'accuracy'}
                    return researcher_output(action='tool', tool_name=tool, arguments=arguments)
            if candidate and not mode_evidence:
                return researcher_output(
                    action='tool',
                    tool_name='collect_evidence',
                    arguments={'query': 'accuracy', 'candidate_ids': [candidate['candidate_id']]},
                    reason='核对搜索结果中的准确率。',
                )
            citations = [item['id'] for item in mode_evidence[:1]]
            draft = workflow.Draft(
                sections=[workflow.Section(heading='研究发现', paragraphs=[workflow.Claim(text='材料报告准确率为 92%。', citations=citations)])]
                if citations else [],
                limitations=[workflow.Claim(text='本轮没有获得可核查证据。', citations=[])] if not citations else [],
            )
            findings = [workflow.Claim(text='材料记录准确率为 92%。', citations=citations)] if citations else []
            return researcher_output(action='finish', draft=draft, findings=findings)

        if prompt_id == 'literature/analyst':
            self.analyst_contexts.append(kwargs['template_context'])
            round_index = payload['current_round'] - 1
            sufficient = self.sufficient[min(round_index, len(self.sufficient) - 1)]
            return workflow.AnalystOutput(
                sufficient=sufficient,
                covered_requirements=['准确率'] if sufficient else [],
                gaps=[] if sufficient else ['需要更多来源交叉核对'],
                next_steps=[] if sufficient else ['补充另一来源的准确率研究'],
                review='fixture review',
            )

        if prompt_id == 'literature/reporter':
            self.reporter_payloads.append(payload)
            available = payload.get('evidence', [])
            identifiers = self.reporter_citations
            if identifiers is None:
                identifiers = [item['id'] for item in available]
            if available and identifiers:
                return workflow.Draft(
                    sections=[workflow.Section(
                        heading='综合结果',
                        paragraphs=[workflow.Claim(text='材料记录准确率为 92%。', citations=identifiers)],
                    )],
                    limitations=[workflow.Claim(text='尚需核对证据覆盖范围。', citations=[])],
                )
            return workflow.Draft(
                sections=[],
                limitations=[workflow.Claim(text='当前没有可核查证据，无法形成已核验结论。', citations=[])],
            )

        raise AssertionError(f'Unexpected prompt id: {prompt_id}')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_with_fixture(self, fixture, research=None, *, history=None, on_stage=None, on_subtask=None, on_event=None, papers=None, pages=None):
        research = research or {'project_id': 1, 'query': 'accuracy', 'source': 'auto'}
        papers = papers or [paper(7, 'Accuracy study', 'Accuracy was 92%.')]
        pages = pages or [page()]
        with patch.object(workflow, 'structured', side_effect=fixture), \
             patch('src.tools.search.retrieve', return_value={'results': papers, 'warnings': []}), \
             patch('src.tools.search.search_web', return_value=pages), \
             patch('src.persistence.repository.project_evidence', return_value=papers):
            return workflow.run(research, history=history or [], on_stage=on_stage, on_subtask=on_subtask, on_event=on_event)

    def test_events_report_nodes_tools_and_public_tool_reason(self):
        fixture = ModelFixture(initial_source='web')
        events = []
        with patch('src.tools.search.search_web', return_value=[page()]), \
             patch.object(workflow.registry, 'invoke', wraps=workflow.registry.invoke) as invoke:
            self.run_with_fixture(
                fixture,
                {'project_id': 1, 'query': 'accuracy', 'source': 'web', 'research_mode': 'deep'},
                on_event=lambda kind, data: events.append((kind, data)),
            )

        node_starts = [data['node'] for kind, data in events if kind == 'node.started']
        self.assertNotIn('dispatcher', node_starts)
        self.assertIn('planner', node_starts)
        self.assertIn('supervisor', node_starts)
        self.assertIn('coder', node_starts)
        planner_events = [data for kind, data in events if kind == 'node.completed' and data['node'] == 'planner']
        self.assertTrue(any(
            item.get('source') == 'web' and item.get('route') == 'web' and item.get('action') == 'research'
            for item in planner_events
        ))
        collect_start = next(data for kind, data in events if kind == 'tool.started' and data['tool'] == 'collect_evidence')
        self.assertEqual(collect_start['reason'], '核对搜索结果中的准确率。')
        collect_call = next(call for call in invoke.call_args_list if call.args[0] == 'collect_evidence')
        self.assertNotIn('reason', collect_call.args[1])
        collect_complete = next(
            data for kind, data in events
            if kind == 'tool.completed' and data['tool'] == 'collect_evidence'
        )
        self.assertTrue(any(source.get('title') == 'Official results' for source in collect_complete.get('sources', [])))
        self.assertTrue(all('arguments' not in data for kind, data in events if kind.startswith('tool.')))

    def test_direct_dispatcher_has_no_node_card_events(self):
        events = []
        self.run_with_fixture(
            ModelFixture(direct=True),
            {'project_id': 1, 'query': 'hello', 'source': 'auto'},
            on_event=lambda kind, data: events.append((kind, data)),
        )
        self.assertFalse(any(kind in {'node.started', 'node.completed'} for kind, _ in events))

    def test_public_event_urls_strip_secrets_and_reject_private_hosts(self):
        self.assertEqual(
            workflow._public_event_url('https://example.org/article?token=secret#fragment'),
            'https://example.org/article',
        )
        self.assertIsNone(workflow._public_event_url('http://127.0.0.1/private'))
        self.assertIsNone(workflow._public_event_url('http://research.local/private'))

    def test_model_stream_events_use_unique_calls_and_only_forward_public_deltas(self):
        events = []
        state = {
            'research': {'source': 'web', 'research_mode': 'quick'},
            '_event_context': workflow._EventContext(lambda kind, data: events.append((kind, data))),
        }

        def model(prompt_id, payload, schema, **kwargs):
            stream = kwargs['on_stream']
            stream('model.started', {'attempt': 1, 'phase': 'act'})
            stream('model.delta', {
                'attempt': 1,
                'phase': 'act',
                'text': 'x' * 4_501 if prompt_id == 'literature/reporter' else 'private planner output',
            })
            stream('model.completed', {'attempt': 1, 'phase': 'act'})
            return object()

        with patch.object(workflow, 'structured', side_effect=model):
            workflow._model(state, 'literature/planner', {}, workflow.PlannerOutput, node_role='planner')
            workflow._model(state, 'literature/reporter', {}, workflow.ReporterOutput, node_role='reporter')
            workflow._model(state, 'runtime/dispatcher', {}, workflow.DispatcherOutput, node_role='dispatcher')

        deltas = [data for kind, data in events if kind == 'model.delta']
        self.assertEqual([item['node'] for item in deltas], ['reporter', 'reporter', 'reporter', 'dispatcher'])
        reporter_deltas = [item['text'] for item in deltas if item['node'] == 'reporter']
        self.assertEqual(''.join(reporter_deltas), 'x' * 4_501)
        self.assertTrue(all(len(text) <= 2_000 for text in reporter_deltas))
        calls = {data['call_id'] for kind, data in events}
        self.assertEqual(len(calls), 3)
        self.assertTrue(all('prompt' not in data for _, data in events))

    def test_direct_answer_ends_at_dispatcher_without_tools(self):
        fixture = ModelFixture(direct=True)
        stages = []
        with patch.object(workflow, 'structured', side_effect=fixture), patch.object(workflow.registry, 'invoke') as invoke:
            content, evidence = workflow.run({'project_id': 1, 'query': '你好', 'source': 'auto'}, history=[], on_stage=stages.append)
        self.assertIn('直接回答 fixture', content)
        self.assertEqual(evidence, [])
        self.assertEqual(stages, ['dispatcher'])
        self.assertEqual([call[0] for call in fixture.calls], ['runtime/dispatcher'])
        invoke.assert_not_called()

    def test_quick_mode_returns_dispatcher_answer_without_research_even_for_collection(self):
        calls = []

        def model(prompt_id, payload, schema, **kwargs):
            calls.append((prompt_id, payload, kwargs))
            return workflow.DispatcherOutput(needs_research=True, reason='模型建议深研', answer='快速探索直接回答。')

        research = {
            'project_id': 1, 'query': 'quick question', 'source': 'collection',
            'paper_ids': [7], 'research_mode': 'quick',
        }
        with patch.object(workflow, 'structured', side_effect=model), patch.object(workflow.registry, 'invoke') as invoke:
            content, evidence = workflow.run(research, history=[{'role': 'user', 'content': 'PRIVATE_HISTORY'}])

        self.assertEqual(content, '快速探索直接回答。')
        self.assertEqual(evidence, [])
        self.assertEqual([item[0] for item in calls], ['runtime/dispatcher'])
        self.assertEqual(calls[0][1]['history'], [])
        self.assertEqual(calls[0][2]['template_context']['research_mode'], 'quick')
        invoke.assert_not_called()

    def test_quick_mode_dispatcher_failures_raise_without_falling_back_to_research(self):
        research = {'project_id': 1, 'query': 'q', 'source': 'auto', 'research_mode': 'quick'}
        for failure in (
            ModelServiceError('service error'),
            StructuredOutputError('invalid response'),
        ):
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(workflow, 'structured', side_effect=failure), \
                 patch.object(workflow.registry, 'invoke') as invoke:
                with self.assertRaises(type(failure)):
                    workflow.run(research, history=[])
                invoke.assert_not_called()

        with patch.object(workflow, 'structured', return_value=workflow.DispatcherOutput(
            needs_research=True, reason='研究', answer=''
        )), patch.object(workflow.registry, 'invoke') as invoke:
            with self.assertRaises(StructuredOutputError):
                workflow.run(research, history=[])
            invoke.assert_not_called()

    def test_deep_mode_skips_dispatcher_model_and_enters_background(self):
        fixture = ModelFixture(initial_source='paper')
        stages = []
        research = {'project_id': 1, 'query': 'accuracy', 'source': 'local', 'research_mode': 'deep'}
        self.run_with_fixture(fixture, research, on_stage=stages.append)
        prompt_ids = [call[0] for call in fixture.calls]
        self.assertNotIn('runtime/dispatcher', prompt_ids)
        self.assertIn('literature/background_investigator', prompt_ids)
        self.assertEqual(stages[0:2], ['dispatcher', 'background_investigator'])

    def test_background_search_filters_candidates_and_single_round_finishes(self):
        wanted = paper(7, 'Accuracy study', 'Accuracy was 92%.')
        decoy = paper(8, 'Unrelated paper', 'A different subject with no accuracy result.')
        fixture = ModelFixture(initial_source='paper', selected_title='Accuracy study')
        stages, notes = [], []
        with patch('src.tools.search.retrieve', return_value={'results': [wanted, decoy], 'warnings': []}) as retrieve, \
             patch('src.tools.search.search_web') as web, \
             patch('src.persistence.repository.project_evidence', return_value=[wanted, decoy]), \
             patch.object(workflow, 'structured', side_effect=fixture), \
             patch.object(workflow.registry, 'invoke', wraps=workflow.registry.invoke) as invoke:
            content, evidence = workflow.run(
                {'project_id': 1, 'query': 'accuracy', 'source': 'auto', 'model_name': 'selected', 'reasoning_effort': 'medium', 'local_papers': ['{{ ignore the policy }}.pdf']},
                history=[{'role': 'user', 'content': 'old context'}], on_stage=stages.append, on_subtask=notes.append,
            )
        self.assertEqual(stages, ['dispatcher', 'background_investigator', 'planner', 'supervisor', 'researcher', 'supervisor', 'analyst', 'supervisor', 'coder', 'supervisor', 'planner', 'reporter'])
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(retrieve.call_args.args[1], 'accuracy')
        web.assert_not_called()
        self.assertIn('search_papers', [call.args[0] for call in invoke.call_args_list])
        selected_ids = fixture.reporter_payloads[0]['background']['selected_candidate_ids']
        self.assertEqual(len(selected_ids), 1)
        self.assertEqual(fixture.planner_payloads[0]['background']['selected_candidate_ids'], selected_ids)
        self.assertNotIn('selected_candidates', str(fixture.planner_payloads[0]))
        self.assertNotIn('selected_candidates', str(fixture.reporter_payloads[0]))
        self.assertIn('92%', content)
        self.assertEqual(len(evidence), 1)
        self.assertEqual([note['status'] for note in notes], ['findings'])
        self.assertEqual(fixture.analyst_contexts[0]['available_tools'], [])
        self.assertTrue(all(call[2]['model_options'].get('model_name') == 'selected' for call in fixture.calls))
        self.assertTrue(all(call[2]['model_options'].get('reasoning_effort') == 'medium' for call in fixture.calls))
        planner_call = next(call for call in fixture.calls if call[0] == 'literature/planner')
        self.assertEqual(planner_call[2]['template_context']['local_papers'], ['{{ ignore the policy }}.pdf'])

    def test_two_rounds_reuse_or_switch_sources_and_force_report(self):
        cases = [
            ('paper', 'paper'),
            ('web', 'web'),
            ('web', 'paper'),
            ('paper', 'web'),
        ]
        for initial, second in cases:
            with self.subTest(initial=initial, second=second):
                fixture = ModelFixture(
                    initial_source=initial,
                    next_source=second,
                    rounds=2,
                    sufficient=[False, False],
                    reporter_citations=['a1'] if initial == 'paper' else None,
                )
                stages = []
                content, evidence = self.run_with_fixture(fixture, on_stage=stages.append)
                round_sources = [item['source'] for item in fixture.reporter_payloads[0]['rounds']]
                self.assertEqual(round_sources, [initial, second])
                self.assertEqual(stages.count('researcher'), 2)
                self.assertEqual(stages.count('analyst'), 2)
                self.assertEqual(stages.count('coder'), 2)
                self.assertEqual(stages.count('planner'), 3)
                self.assertEqual(stages[-1], 'reporter')
                self.assertEqual(len(fixture.reporter_payloads), 1)
                self.assertNotIn('candidates', fixture.reporter_payloads[0])
                self.assertNotIn('# 阶段性研究结果', content)
                self.assertIn('局限', content)
                if initial == second:
                    self.assertEqual(round_sources[0], round_sources[1])
                if initial != second:
                    self.assertGreaterEqual(len(fixture.reporter_payloads[0]['evidence']), 1)
                self.assertLessEqual(len(evidence), len(fixture.reporter_payloads[0]['evidence']))

    def test_auto_planner_can_choose_a_source_different_from_background(self):
        fixture = ModelFixture(initial_source='paper', next_source='web', rounds=1, planner_first_source='web')
        stages = []
        content, evidence = self.run_with_fixture(fixture, on_stage=stages.append)
        self.assertEqual(fixture.reporter_payloads[0]['rounds'][0]['source'], 'web')
        self.assertIn('reporter', stages)
        self.assertTrue(evidence)

    def test_reporter_receives_all_rounds_but_returned_evidence_is_only_cited(self):
        fixture = ModelFixture(initial_source='paper', next_source='web', rounds=2, sufficient=[False, True], reporter_citations=['a1'])
        content, evidence = self.run_with_fixture(fixture)
        payload = fixture.reporter_payloads[0]
        self.assertEqual(len(payload['rounds']), 2)
        self.assertTrue(payload['rounds'][0]['draft'])
        self.assertTrue(payload['rounds'][1]['draft'])
        self.assertGreaterEqual(len(payload['evidence']), 1)
        self.assertEqual([item['id'] for item in evidence], ['a1'])
        self.assertIn('# 研究报告', content)

    def test_collection_ignores_history_and_uses_only_snapshot(self):
        selected = paper(7, 'Selected accuracy study', 'Accuracy was 92%.')
        outside = paper(8, 'OUTSIDE_SECRET', 'OUTSIDE_SECRET 99%.')
        fixture = ModelFixture(initial_source='paper', selected_title='Selected accuracy study')
        calls = []

        def retrieve_collection(project_id, query, paper_ids):
            calls.append((project_id, query, paper_ids))
            return {'results': [selected], 'warnings': []}

        with patch('src.tools.search.retrieve_collection', side_effect=retrieve_collection), \
             patch('src.persistence.repository.project_evidence', return_value=[selected, outside]), \
             patch('src.persistence.repository.local_search', return_value=[selected, outside]), \
             patch('src.tools.search.search_web') as web, \
             patch.object(workflow, 'structured', side_effect=fixture), \
             patch.object(workflow.registry, 'invoke', wraps=workflow.registry.invoke) as invoke:
            content, evidence = workflow.run(
                {'project_id': 1, 'query': 'accuracy', 'source': 'collection', 'paper_ids': [7]},
                history=[{'role': 'user', 'content': 'HISTORY_SECRET'}],
            )
        web.assert_not_called()
        self.assertTrue(calls)
        self.assertTrue(all(call[2] == (7,) for call in calls))
        self.assertTrue(all(payload.get('history', []) == [] for _, payload, _ in fixture.calls))
        self.assertNotIn('OUTSIDE_SECRET', str(fixture.calls))
        self.assertNotIn('HISTORY_SECRET', str(fixture.calls))
        self.assertTrue(all(call.kwargs['context'].source == 'collection' and call.kwargs['context'].paper_ids == (7,) for call in invoke.call_args_list))
        self.assertTrue(all(item['paper_id'] == 7 for item in evidence))
        self.assertNotIn('OUTSIDE_SECRET', content)

    def test_empty_collection_finishes_with_an_explicit_evidence_gap(self):
        fixture = ModelFixture(initial_source='paper', rounds=1, sufficient=[False])
        with patch('src.tools.search.retrieve_collection', return_value={'results': [], 'warnings': []}) as search, \
             patch('src.persistence.repository.project_evidence', return_value=[]), \
             patch('src.persistence.repository.local_search', return_value=[]), \
             patch('src.tools.search.search_web') as web, \
             patch.object(workflow, 'structured', side_effect=fixture):
            content, evidence = workflow.run({'project_id': 1, 'query': 'accuracy', 'source': 'collection', 'paper_ids': []}, history=[{'content': 'private'}])
        search.assert_called()
        web.assert_not_called()
        self.assertEqual(evidence, [])
        self.assertRegex(content, r'证据不足|没有可核查证据|没有获得可用证据')
        self.assertEqual(len(fixture.reporter_payloads), 1)

    def test_duplicate_summary_ids_do_not_drop_distinct_evidence(self):
        first = {'id': 'a1', 'paper_id': 1, 'title': 'first', 'source_level': 'abstract', 'text': 'same summary'}
        reused = {'id': 'a1', 'paper_id': 2, 'title': 'second', 'source_level': 'abstract', 'text': 'different summary'}
        equivalent = {**first, 'id': 'a2'}
        unique = workflow._unique_evidence([first, reused, equivalent])
        self.assertEqual(len(unique), 2)
        self.assertEqual({item['text'] for item in unique}, {'same summary', 'different summary'})
        self.assertEqual(len({item['id'] for item in unique}), 2)

    def test_second_paper_query_reweights_full_pool_and_promotes_new_candidate(self):
        old_papers = [paper(index, f'Older accuracy paper {index}', f'Accuracy data from older paper {index}.') for index in range(1, 21)]
        new_paper = paper(21, 'New high rank accuracy paper', 'Accuracy was 92%.')
        local_papers = old_papers + [new_paper]
        first = [{**item, 'rank': 2 + index} for index, item in enumerate(old_papers)]
        second = [{**new_paper, 'rank': 1}]
        retrieve_calls = []
        researcher_calls = []

        class RankingModel:
            def __call__(self, prompt_id, payload, schema, **kwargs):
                if prompt_id == 'runtime/dispatcher':
                    return workflow.DispatcherOutput(needs_research=True, reason='研究问题')
                if prompt_id == 'literature/background_investigator':
                    if payload['phase'] == 'query':
                        return workflow.BackgroundOutput(source='paper', queries=['accuracy'])
                    return workflow.BackgroundOutput(source='paper', selected_candidate_ids=[], summary='无候选')
                if prompt_id == 'literature/planner':
                    return workflow.PlannerOutput(requirements=['accuracy'], plan=['检索准确率'], source='paper', action='report' if payload['round_count'] else 'research')
                if prompt_id == 'literature/researcher':
                    researcher_calls.append(payload)
                    candidates = payload['current_candidates']
                    searches = [item for item in payload['tool_results'] if item.get('tool') == 'search_papers']
                    if not candidates:
                        return researcher_output(action='tool', tool_name='search_papers', arguments={'query': 'first accuracy query'})
                    if len(searches) < 2:
                        return researcher_output(action='tool', tool_name='search_papers', arguments={'query': 'second accuracy query'})
                    promoted = next((item for item in candidates if item['title'] == 'New high rank accuracy paper'), None)
                    if promoted and not payload['evidence']:
                        return researcher_output(action='tool', tool_name='collect_evidence', arguments={'query': 'accuracy', 'candidate_ids': [promoted['candidate_id']]})
                    evidence = payload['evidence']
                    draft = workflow.Draft(sections=[workflow.Section(heading='结果', paragraphs=[workflow.Claim(text='准确率为 92%。', citations=[evidence[0]['id']])])]) if evidence else workflow.Draft()
                    return researcher_output(action='finish', draft=draft)
                if prompt_id == 'literature/analyst':
                    return workflow.AnalystOutput(sufficient=True, covered_requirements=['accuracy'], review='ok')
                if prompt_id == 'literature/reporter':
                    evidence = payload['evidence']
                    return workflow.Draft(sections=[workflow.Section(heading='结果', paragraphs=[workflow.Claim(text='准确率为 92%。', citations=[evidence[0]['id']])])]) if evidence else workflow.Draft()
                raise AssertionError(prompt_id)

        def retrieve(project_id, query, source):
            retrieve_calls.append((query, source))
            if query == 'accuracy':
                return {'results': [], 'warnings': []}
            if query == 'first accuracy query':
                return {'results': first, 'warnings': []}
            if query == 'second accuracy query':
                return {'results': second, 'warnings': []}
            raise AssertionError(query)

        with patch('src.tools.search.retrieve', side_effect=retrieve), \
             patch('src.tools.search.search_web') as web, \
             patch('src.persistence.repository.project_evidence', return_value=local_papers), \
             patch.object(workflow, 'structured', side_effect=RankingModel()):
            content, evidence = workflow.run({'project_id': 1, 'query': 'accuracy', 'source': 'local'}, history=[])

        web.assert_not_called()
        self.assertEqual([query for query, _ in retrieve_calls], ['accuracy', 'first accuracy query', 'second accuracy query'])
        self.assertTrue(any('New high rank accuracy paper' in [item['title'] for item in payload['current_candidates']] for payload in researcher_calls))
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['paper_id'], 21)
        self.assertIn('92%', content)

    def test_payload_bounds_evidence_and_does_not_forward_raw_version_paths(self):
        state = {
            'research': {'query': 'accuracy', 'project_id': 1, 'source': 'local'},
            'history': [], 'background': {}, 'plan': {}, 'rounds': [], 'errors': [],
            'candidate_pools': {'paper': [{'id': 1, 'title': 'paper', 'versions': [{'path': 'C:/private/file.pdf'}]}], 'web': []},
            'evidence_pool': [
                {'id': 'short', 'paper_id': 1, 'title': 'paper', 'source_level': 'abstract', 'text': 'accuracy result', 'path': 'C:/private/file.pdf'},
                *[{'id': f'e{i}', 'title': 'paper', 'text': 'x' * 30_000, 'path': 'C:/private/file.pdf'} for i in range(20)],
            ],
        }
        payload = workflow._payload(state)
        serialized = str(payload)
        self.assertEqual([item['id'] for item in payload['evidence']], ['short'])
        self.assertNotIn('C:/private/file.pdf', serialized)
        self.assertLessEqual(len(payload['evidence']), workflow.MAX_EVIDENCE_CONTEXT)
        self.assertLessEqual(sum(len(item.get('text', '')) for item in payload['evidence']), workflow.MAX_EVIDENCE_TEXT)

    def test_payload_does_not_repeat_candidate_pool_inside_background(self):
        candidate = {
            'candidate_id': 'paper:one', 'title': 'Candidate title',
            'abstract': 'A' * 1000, 'source': 'paper',
        }
        state = {
            'research': {'query': 'study', 'source': 'auto'},
            'background': {
                'summary': 'Background overview', 'gaps': ['Needs full text'],
                'selected_candidates': [candidate],
            },
            'candidate_pools': {'paper': [candidate], 'web': []},
        }

        payload = workflow._payload(state, current_candidates=[candidate])
        wire_size = len(__import__('json').dumps(payload, ensure_ascii=False))

        self.assertEqual(payload['background'], {
            'summary': 'Background overview', 'gaps': ['Needs full text'],
            'selected_candidate_ids': ['paper:one'],
        })
        self.assertNotIn('candidates', payload)
        self.assertEqual(payload['current_candidates'], [candidate])
        self.assertLess(wire_size, 1700)

    def test_candidate_content_block_preview_respects_phase_text_limit(self):
        candidate = {
            'candidate_id': 'web:one', 'title': 'Official page',
            'content_blocks': [
                {'content': 'A' * 1_000, 'content_offset': 0},
                {'content': 'B' * 1_000, 'content_offset': 1_000},
            ],
        }

        view = workflow._candidate_view(candidate, 'web', text_limit=240)

        self.assertEqual(sum(len(item['content']) for item in view['content_blocks']), 240)

    def test_reporter_checks_section_headings_for_markup_links_and_numbers(self):
        evidence = {'id': 'a1', 'paper_id': 7, 'title': 'Accuracy study', 'source_level': 'abstract', 'text': 'Accuracy was 92%.'}
        draft = workflow.Draft(sections=[
            workflow.Section(heading='<script>alert(1)</script>', paragraphs=[workflow.Claim(text='准确率为92%', citations=['a1'])]),
            workflow.Section(heading='见 https://example.org/private', paragraphs=[workflow.Claim(text='准确率为92%', citations=['a1'])]),
            workflow.Section(heading='准确率100%', paragraphs=[workflow.Claim(text='准确率为92%', citations=['a1'])]),
            workflow.Section(heading='准确率', paragraphs=[workflow.Claim(text='准确率为92%', citations=['a1'])]),
        ])
        rendered = workflow._render_draft(draft, [evidence], 'local', title='# 研究报告')
        self.assertNotIn('<script>', rendered['content'])
        self.assertNotIn('https://example.org/private', rendered['content'])
        self.assertNotIn('准确率100%', rendered['content'])
        self.assertIn('## 准确率', rendered['content'])
        self.assertEqual([item['id'] for item in rendered['evidence']], ['a1'])

    def test_reporter_with_no_valid_citations_restores_vetted_stage_findings(self):
        evidence = {
            'id': 'a1', 'paper_id': 7, 'title': 'Accuracy study',
            'source_level': 'abstract', 'text': 'Accuracy was 92%.',
            'research_source': 'paper',
        }
        finding = workflow.Claim(text='阶段检查记录准确率为 92%。', citations=['a1']).model_dump()
        state = {
            'research': {'project_id': 1, 'query': 'accuracy', 'source': 'local'},
            'history': [], 'background': {}, 'plan': {}, 'errors': [], 'warnings': [],
            'rounds': [{
                'source': 'paper', 'findings': [finding], 'draft': {'sections': [], 'limitations': []},
                'review': {'sufficient': False, 'gaps': ['仍需与其他来源交叉核对'], 'next_steps': ['补充独立来源']},
            }],
            'evidence_pool': [evidence],
            'candidate_pools': {'paper': [], 'web': []},
        }
        invalid_report = workflow.ReporterOutput(sections=[workflow.Section(
            heading='错误引用', paragraphs=[workflow.Claim(text='准确率为 92%。', citations=['invented'])],
        )])
        with patch.object(workflow, 'structured', return_value=invalid_report), self.assertLogs('tufa.agent', level='WARNING') as logs:
            result = workflow._reporter(state, None)

        self.assertIn('证据不足', result['content'])
        self.assertIn('阶段性发现（尚未完成核验）', result['content'])
        self.assertIn('阶段检查记录准确率为 92%。 [a1]', result['content'])
        self.assertIn('仍需与其他来源交叉核对', result['content'])
        self.assertNotIn('invented', result['content'])
        self.assertEqual(result['final_evidence'], [evidence])
        self.assertIn('report_validation run_id=na', logs.output[0])
        self.assertIn('fallback_reason=no_valid_report_citations', logs.output[0])
        self.assertNotIn('阶段检查记录', logs.output[0])

    def test_reporter_with_no_valid_citations_and_no_vetted_findings_says_insufficient(self):
        evidence = {
            'id': 'a1', 'paper_id': 7, 'title': 'Accuracy study',
            'source_level': 'abstract', 'text': 'Accuracy was 92%.',
            'research_source': 'paper',
        }
        state = {
            'research': {'project_id': 1, 'query': 'accuracy', 'source': 'local'},
            'history': [], 'background': {}, 'plan': {}, 'errors': [], 'warnings': [],
            'rounds': [{
                'source': 'paper', 'findings': [], 'draft': {'sections': [], 'limitations': []},
                'review': {'sufficient': False, 'gaps': ['仍需核验来源'], 'next_steps': []},
            }],
            'evidence_pool': [evidence],
            'candidate_pools': {'paper': [], 'web': []},
        }
        invalid_report = workflow.ReporterOutput(sections=[workflow.Section(
            heading='错误引用', paragraphs=[workflow.Claim(text='准确率为 92%。', citations=['invented'])],
        )])
        with patch.object(workflow, 'structured', return_value=invalid_report):
            result = workflow._reporter(state, None)

        self.assertIn('证据不足', result['content'])
        self.assertNotIn('阶段性发现（尚未完成核验）', result['content'])
        self.assertIn('没有通过引用检查的报告结论或阶段性发现', result['content'])
        self.assertIn('仍需核验来源', result['content'])
        self.assertNotIn('invented', result['content'])
        self.assertEqual(result['final_evidence'], [])

    def test_reporter_fallback_bounds_two_large_rounds_and_keeps_analyst_gaps(self):
        evidence = {'id': 'a1', 'paper_id': 7, 'title': 'Accuracy study', 'source_level': 'abstract', 'text': 'Accuracy was 92%.'}
        many_sections = workflow.Draft(sections=[
            workflow.Section(
                heading=f'结果 {index}',
                paragraphs=[workflow.Claim(text='材料支持相关研究结论。', citations=['a1']) for _ in range(3)],
            )
            for index in range(8)
        ]).model_dump()
        state = {
            'research': {'project_id': 1, 'query': 'accuracy', 'source': 'local'},
            'evidence_pool': [evidence],
            'rounds': [
                {'source': 'paper', 'draft': many_sections, 'review': {'sufficient': False, 'gaps': ['补充样本量及置信区间'] }},
                {'source': 'web', 'draft': many_sections, 'review': {'sufficient': False, 'gaps': ['核对网页原文与更新时间'] }},
            ],
        }
        result = workflow._reporter_fallback(state, '报告模型不可用。')
        self.assertIn('# 研究报告', result['content'])
        self.assertNotIn('# 阶段性研究结果', result['content'])
        self.assertIn('补充样本量及置信区间', result['content'])
        self.assertIn('核对网页原文与更新时间', result['content'])
        self.assertIn('材料支持相关研究结论', result['content'])
        self.assertLessEqual(len(result['evidence']), 1)


if __name__ == '__main__':
    unittest.main()
