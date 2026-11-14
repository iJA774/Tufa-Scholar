import unittest
import os
import tempfile
from unittest.mock import patch

from src.agents import literature as workflow


WEB_PAGE = {
    'title': 'Official accuracy results',
    'url': 'https://example.org/accuracy',
    'content': 'Accuracy was 92%.',
    'source_level': 'web_snippet',
    'provider': 'fixture',
    'retrieved_at': '2026-11-12T10:00:00+08:00',
}


class WebWorkflowModel:
    def __init__(self, *, rounds=1, research_search_limit=None):
        self.rounds = rounds
        self.research_search_limit = research_search_limit
        self.research_searches = 0
        self.calls = []
        self.reporter_payloads = []

    def __call__(self, prompt_id, payload, schema, **kwargs):
        self.calls.append((prompt_id, payload, kwargs))
        if prompt_id == 'runtime/dispatcher':
            return workflow.DispatcherOutput(needs_research=True, reason='网页资料需查证')
        if prompt_id == 'literature/background_investigator':
            if payload['phase'] == 'query':
                return workflow.BackgroundOutput(source='web', queries=['accuracy official results'])
            candidates = payload.get('candidates', [])
            return workflow.BackgroundOutput(
                source='web',
                selected_candidate_ids=[candidates[0]['candidate_id']] if candidates else [],
                summary='网页候选已筛选。' if candidates else '未找到网页候选。',
                gaps=[] if candidates else ['无可用网页候选'],
            )
        if prompt_id == 'literature/planner':
            count = payload['round_count']
            return workflow.PlannerOutput(
                requirements=['核实准确率'],
                plan=['搜索官方准确率结果'],
                source='web',
                action='research' if count < self.rounds else 'report',
                rationale='网页 fixture',
            )
        if prompt_id == 'literature/researcher':
            candidates = payload.get('current_candidates', [])
            evidence = [item for item in payload.get('evidence', []) if item.get('source_level') in {'web_snippet', 'web_content'}]
            if not candidates and (self.research_search_limit is None or self.research_searches < self.research_search_limit):
                self.research_searches += 1
                return workflow.ResearcherOutput(
                    action='tool', tool_name='search_web',
                    arguments={'query': 'accuracy official results', 'limit': 5},
                )
            if candidates and not evidence:
                return workflow.ResearcherOutput(
                    action='tool', tool_name='collect_evidence',
                    arguments={'query': 'accuracy', 'candidate_ids': [candidates[0]['candidate_id']]},
                )
            draft = workflow.Draft(
                sections=[workflow.Section(heading='网页资料', paragraphs=[workflow.Claim(text='搜索结果报告准确率为 92%。', citations=[evidence[0]['id']])])]
                if evidence else [],
                limitations=[workflow.Claim(text='当前没有网页证据可用于核验。', citations=[])] if not evidence else [],
            )
            return workflow.ResearcherOutput(action='finish', draft=draft)
        if prompt_id == 'literature/analyst':
            return workflow.AnalystOutput(
                sufficient=False,
                covered_requirements=[],
                gaps=['仍需核对页面原文和网页获取时间'],
                next_steps=['尝试读取网页正文或查询其他来源'],
                review='网页搜索摘要不是全文。',
            )
        if prompt_id == 'literature/reporter':
            self.reporter_payloads.append(payload)
            evidence = payload.get('evidence', [])
            if not evidence:
                return workflow.Draft(sections=[], limitations=[workflow.Claim(text='当前没有可核查的网页证据。', citations=[])])
            return workflow.Draft(
                sections=[workflow.Section(heading='检索结果', paragraphs=[workflow.Claim(text='搜索摘要报告准确率为 92%。', citations=[evidence[0]['id']])])],
                limitations=[workflow.Claim(text='搜索摘要不代表完整网页正文。', citations=[evidence[0]['id']])],
            )
        raise AssertionError(prompt_id)


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_web_background_search_collects_snippet_with_source_metadata(self):
        model = WebWorkflowModel()
        with patch.object(workflow, 'structured', side_effect=model), \
             patch('src.tools.search.search_web', return_value=[WEB_PAGE]) as search, \
             patch.object(workflow.registry, 'invoke', wraps=workflow.registry.invoke) as invoke:
            content, evidence = workflow.run(
                {'project_id': 2, 'query': 'accuracy', 'source': 'web'},
                history=[],
            )
        self.assertEqual(search.call_count, 1)
        self.assertIn('search_web', [call.args[0] for call in invoke.call_args_list])
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['source_level'], 'web_snippet')
        self.assertEqual(evidence[0]['provider'], 'fixture')
        self.assertEqual(evidence[0]['original_url'], WEB_PAGE['url'])
        self.assertIn(WEB_PAGE['url'], content)
        self.assertIn('搜索摘要不代表完整网页正文', content)

    def test_repeated_search_failure_still_reaches_final_no_evidence_report(self):
        model = WebWorkflowModel(rounds=2, research_search_limit=2)
        stages = []
        with patch.object(workflow, 'structured', side_effect=model), \
             patch('src.tools.search.search_web', side_effect=RuntimeError('private search failure')) as search:
            content, evidence = workflow.run(
                {'project_id': 2, 'query': 'accuracy', 'source': 'web'},
                history=[], on_stage=stages.append,
            )
        self.assertGreaterEqual(search.call_count, 3)
        self.assertEqual(stages.count('researcher'), 2)
        self.assertEqual(stages.count('analyst'), 2)
        self.assertEqual(len(model.reporter_payloads), 1)
        self.assertEqual(model.reporter_payloads[0]['evidence'], [])
        self.assertEqual(evidence, [])
        self.assertNotIn('private search failure', content)
        self.assertRegex(content, r'证据不足|没有可核查证据|没有获得可用证据')
        self.assertNotIn('# 阶段性研究结果', content)

    def test_successful_forced_rewrite_resets_consecutive_search_failures(self):
        model_calls = []

        class RetryModel:
            def __call__(self, prompt_id, payload, schema, **kwargs):
                model_calls.append((prompt_id, payload))
                if prompt_id == 'runtime/dispatcher':
                    return workflow.DispatcherOutput(needs_research=True, reason='需要网页证据')
                if prompt_id == 'literature/background_investigator':
                    if payload['phase'] == 'query':
                        return workflow.BackgroundOutput(source='web', queries=['accuracy'])
                    return workflow.BackgroundOutput(source='web', summary='没有候选', gaps=['暂无候选'])
                if prompt_id == 'literature/planner':
                    return workflow.PlannerOutput(
                        requirements=['核实准确率'], plan=['accuracy plan'], source='web',
                        action='research' if payload['round_count'] == 0 else 'report', rationale='fixture',
                    )
                if prompt_id == 'literature/researcher':
                    if payload['phase'] == 'draft':
                        return workflow.ResearcherOutput(
                            action='finish',
                            draft=workflow.Draft(limitations=[workflow.Claim(text='没有取得可核查证据。', citations=[])]),
                        )
                    act_count = sum(phase.get('phase') == 'act' for role, phase in model_calls if role == 'literature/researcher')
                    if act_count in {1, 3}:
                        return workflow.ResearcherOutput(
                            action='tool', tool_name='search_web',
                            arguments={'query': f'attempt {act_count}', 'limit': 5},
                        )
                    return workflow.ResearcherOutput(action='finish')
                if prompt_id == 'literature/analyst':
                    return workflow.AnalystOutput(sufficient=False, gaps=['无证据'], review='资料不足。')
                if prompt_id == 'literature/reporter':
                    return workflow.ReporterOutput(limitations=[workflow.Claim(text='没有取得可核查证据。', citations=[])])
                raise AssertionError(prompt_id)

        search_responses = [[], RuntimeError('first failure'), [], RuntimeError('later failure')]
        with patch.object(workflow, 'structured', side_effect=RetryModel()), \
             patch('src.tools.search.search_web', side_effect=search_responses) as search:
            content, evidence = workflow.run(
                {'project_id': 2, 'query': 'accuracy', 'source': 'web'}, history=[],
            )

        researcher_payloads = [payload for role, payload in model_calls if role == 'literature/researcher']
        self.assertEqual([payload['phase'] for payload in researcher_payloads], ['act', 'act', 'act', 'act', 'draft'])
        self.assertEqual(search.call_count, 4)
        self.assertEqual(evidence, [])
        self.assertRegex(content, r'证据不足|没有可核查证据|没有获得可用证据')

    def test_repeated_failed_web_read_is_skipped_and_round_stops(self):
        candidate = {
            'candidate_id': 'web:inaccessible',
            'title': 'Inaccessible page',
            'url': 'https://example.org/inaccessible',
            'original_url': 'https://example.org/inaccessible',
        }
        model_calls = []

        def model(prompt_id, payload, schema, **kwargs):
            model_calls.append((prompt_id, payload))
            if payload.get('phase') == 'act':
                return workflow.ResearcherOutput(
                    action='tool', tool_name='read_web_page',
                    arguments={'candidate_id': 'web:inaccessible', 'offset': 0},
                )
            return workflow.ResearcherOutput(
                action='finish',
                draft=workflow.Draft(limitations=[workflow.Claim(text='网页无法读取，证据不足。', citations=[])]),
            )

        state = {
            'research': {'project_id': 1, 'query': 'accuracy', 'source': 'web'},
            'current_source': 'web',
            'current_round': 1,
            'current_plan': ['读取网页正文'],
            'plan': {'requirements': ['核实准确率']},
            'background': {},
            'candidate_pools': {'paper': [], 'web': [candidate]},
            'evidence_pool': [],
            'rounds': [],
            'errors': [],
            'warnings': [],
            'tool_results': [],
        }
        with patch.object(workflow, 'structured', side_effect=model), \
             patch.object(workflow.registry, 'invoke', side_effect=workflow.WebPageError(category='timeout')) as invoke:
            result = workflow._researcher(state, None)

        self.assertEqual(invoke.call_count, 1)
        self.assertEqual([payload['phase'] for _, payload in model_calls], ['act', 'act', 'draft'])
        self.assertEqual(result['current_draft']['limitations'][0]['text'], '网页无法读取，证据不足。')
        self.assertEqual(result['errors'][-2]['code'], 'web_page_read_failed')
        self.assertEqual(result['errors'][-1]['code'], 'duplicate_web_read_skipped')
        self.assertNotIn(candidate['url'], str(result['errors']))


if __name__ == '__main__':
    unittest.main()
