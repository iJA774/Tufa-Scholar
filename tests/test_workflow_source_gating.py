import unittest
from unittest.mock import patch

from src.agents import literature


class SourceGatingWorkflowTests(unittest.TestCase):
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
        search.assert_called_once()
        self.assertEqual(search.call_args.args[2], 'local')

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
