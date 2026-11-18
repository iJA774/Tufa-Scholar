import unittest
from unittest.mock import patch

from src.agents import literature


class SourceGatingWorkflowTests(unittest.TestCase):
    def test_mixed_search_reserves_local_calls_after_general_budget_is_spent(self):
        research = {
            'project_id': 1, 'query': 'question', 'source': 'auto',
            'network_search': True, 'local_literature': True, 'research_depth': 'standard',
        }
        state = {'research': research, 'research_actions_used': 19, 'research_local_actions_used': 3}
        decisions = iter([
            ('search_papers', {'query': 'test'}),
            ('select_local_domains', {'domains': ['Area']}),
            ('collect_evidence', {'query': 'test', 'candidate_ids': ['remote']}),
            ('read_local_markdown', {'document_id': 'local', 'query': 'test'}),
            ('read_local_markdown', {'document_id': 'local', 'query': 'other'}),
        ])
        snapshots = []

        def model(_state, _prompt, payload, _schema, **kwargs):
            if payload['phase'] == 'draft':
                return '> 局限：资料不足。'
            snapshots.append((payload['calls_remaining'], payload['local_calls_remaining'],
                              set(payload['free_tools']), set(payload['metered_tools'])))
            decision = next(decisions, None)
            if decision is None:
                return literature.ResearchActionOutput(action='finish')
            name, arguments = decision
            return literature.ResearchActionOutput(action='tool', tool_name=name, arguments=arguments)

        with patch('src.tools.local_markdown.list_local_domains', return_value=['Area']), \
             patch.object(literature, '_model', side_effect=model), \
             patch.object(literature, 'structured_input_units', return_value=1), \
             patch.object(literature, '_invoke_tool_with_events', return_value=({}, {})) as execute:
            result = literature._researcher(state, None)
        self.assertEqual([(general, local) for general, local, _, _ in snapshots],
                         [(1, 2), (0, 2), (0, 2), (0, 2), (0, 1), (0, 0)])
        self.assertEqual(snapshots[1][2], {'select_local_domains', 'collect_evidence'})
        self.assertEqual(snapshots[1][3], {'read_local_markdown'})
        self.assertEqual(snapshots[-1][3], set())
        self.assertEqual(execute.call_count, 5)
        self.assertEqual(result['research_actions_used'], 20)
        self.assertEqual(result['research_local_actions_used'], 5)
        self.assertEqual(result['round_tool_actions'], 3)

    def test_disabled_network_changes_mode_and_never_calls_background_web(self):
        research = {'project_id': 1, 'query': 'question', 'source': 'auto',
                    'network_search': False, 'local_literature': True}
        self.assertEqual(literature._available_modes(research), ['paper'])
        response = literature.BackgroundOutput(source='web', queries=['question'])
        with patch.object(literature, '_model', return_value=response), \
             patch('src.tools.search.retrieve', return_value={'results': [], 'warnings': []}) as search, \
             patch('src.tools.search.search_web', side_effect=AssertionError('network called')):
            result = literature._background_investigator({'research': research}, None)
        self.assertEqual(result['background']['source'], 'paper')
        search.assert_not_called()

    def test_local_disclosure_precedes_read_and_evidence_retains_offsets(self):
        research = {'project_id': 1, 'query': 'question', 'source': 'local'}
        state = {'research': research}
        document = {'document_id': 'doc-1', 'title': 'Title', 'domains': ['Area']}
        evidence = {'id': 'e1', 'document_id': 'doc-1', 'paper_id': 3,
                    'title': 'Title', 'text': 'Relevant', 'source_level': 'local_markdown',
                    'char_start': 10, 'char_end': 18}
        with patch('src.tools.local_markdown.list_local_domains', return_value=['Area']), \
             patch.object(literature.registry, 'invoke', side_effect=[
                 {'documents': [document], 'next_offset': 1}, [evidence]]):
            with self.assertRaises(literature.ToolCallValidationError):
                literature._invoke_tool(state, 'paper', 'read_local_markdown',
                                        {'document_id': 'doc-1', 'query': 'question'})
            with self.assertRaises(literature.ToolCallValidationError):
                literature._invoke_tool(state, 'paper', 'select_local_domains', {'domains': ['Other']})
            patch_result, disclosed = literature._invoke_tool(state, 'paper', 'select_local_domains', {'domains': ['Area']})
            self.assertEqual(disclosed['next_offset'], 1)
            state.update(patch_result)
            patch_result, read = literature._invoke_tool(state, 'paper', 'read_local_markdown',
                                                        {'document_id': 'doc-1', 'query': 'question', 'offset': 10})
            self.assertEqual(read[0]['char_start'], 10)
            self.assertEqual(literature._evidence_views(patch_result['evidence_pool'])[0]['document_id'], 'doc-1')

    def test_disabled_local_uses_remote_papers_and_honors_collection_scope(self):
        research = {'project_id': 1, 'query': 'question', 'source': 'auto',
                    'network_search': True, 'local_literature': False}
        response = literature.BackgroundOutput(source='paper', queries=['question'])
        with patch.object(literature, '_model', return_value=response), \
             patch('src.tools.search.retrieve', return_value={'results': [], 'warnings': []}) as search:
            literature._background_investigator({'research': research}, None)
        self.assertEqual(search.call_args.args[2], 'openalex')
        collection = {**research, 'source': 'collection', 'paper_ids': [1]}
        self.assertEqual(literature._catalog_for_mode(collection, 'paper'), [])

    def test_disabled_sources_skip_action_but_still_generate_deep_draft(self):
        state = {'research': {'project_id': 1, 'query': 'question', 'source': 'auto',
                              'network_search': False, 'local_literature': False,
                              'research_depth': 'deep'}}
        phases = []

        def model(_state, _prompt, payload, _schema, **_kwargs):
            phases.append(payload['phase'])
            return 'Draft.'

        with patch.object(literature, '_model', side_effect=model), \
             patch.object(literature.registry, 'invoke', side_effect=AssertionError('source called')):
            result = literature._researcher(state, None)
        self.assertEqual(phases, ['draft'])
        self.assertEqual(result['round_tool_actions'], 0)

    def test_both_disabled_skip_background_search_even_with_collection_scope(self):
        for source in ('auto', 'collection'):
            with self.subTest(source=source):
                research = {'project_id': 1, 'query': 'question', 'source': source,
                            'paper_ids': [1], 'network_search': False, 'local_literature': False}
                response = literature.BackgroundOutput(source='paper', queries=['question'])
                with patch.object(literature, '_model', return_value=response), \
                     patch.object(literature.registry, 'invoke', side_effect=AssertionError('source called')):
                    result = literature._background_investigator({'research': research}, None)
                self.assertEqual(result['candidate_pools'], {'paper': [], 'web': []})
                self.assertEqual(literature._catalog_for_mode(research, 'paper'), [])


if __name__ == '__main__':
    unittest.main()
