import unittest
from unittest.mock import patch

from pydantic import BaseModel

from src.tools import ToolContext, ToolRegistry


class ResearchToolsTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.local = ToolContext(1, 'local')
        self.collection = ToolContext(1, 'collection', [2, 3, 99])
        self.web = ToolContext(1, 'web')

    def test_explicit_registration_and_source_restrictions(self):
        class CustomInput(BaseModel):
            value: int

        def handler(params, context):
            return {'value': params.value, 'project_id': context.project_id}

        self.registry.register('custom', '自定义工具', CustomInput, handler, {'local'})
        tool = next(item for item in self.registry.list_tools(self.local) if item['name'] == 'custom')
        self.assertEqual(tool['description'], '自定义工具')
        self.assertIn('value', tool['input_schema']['properties'])
        self.assertEqual(self.registry.invoke('custom', {'value': 7}, context=self.local), {'value': 7, 'project_id': 1})
        self.assertNotIn('custom', {item['name'] for item in self.registry.list_tools(self.web)})
        with self.assertRaises(ValueError):
            self.registry.invoke('custom', {'value': 7}, context=self.web)
        with self.assertRaises(ValueError):
            self.registry.register('custom', '重复工具', CustomInput, handler, {'web'})

    def test_context_copies_mutable_paper_ids(self):
        paper_ids = [2, 3]
        context = ToolContext(1, 'collection', paper_ids)
        paper_ids.append(4)
        paper_ids[0] = 99
        self.assertEqual(context.paper_ids, (2, 3))

    def test_discovery_and_execution_respect_source(self):
        local_names = {item['name'] for item in self.registry.list_tools(self.local)}
        web_tools = self.registry.list_tools(self.web)
        self.assertEqual(local_names, {'search_papers', 'collect_evidence'})
        self.assertEqual({item['name'] for item in web_tools}, {'search_web', 'collect_evidence'})
        self.assertTrue(all(item['description'] and item['input_schema']['properties'] for item in web_tools))
        with self.assertRaises(ValueError):
            self.registry.invoke('search_web', {'query': 'x'}, context=self.local)
        with self.assertRaises(ValueError):
            self.registry.invoke('missing', {}, context=self.web)

    def test_validation_does_not_echo_input(self):
        for arguments in ({'query': 'secret', 'source': 'all'}, {'query': '  '}, {'query': 'secret', 'limit': 6}):
            with self.assertRaises(ValueError) as error:
                self.registry.invoke('search_web', arguments, context=self.web)
            self.assertNotIn('secret', str(error.exception))
        with self.assertRaises(ValueError) as error:
            self.registry.invoke('collect_evidence', {'query': 'x', 'papers': [{'title': 'secret'}]}, context=self.web)
        self.assertNotIn('secret', str(error.exception))

    @patch('src.tools.search.retrieve')
    def test_direct_paper_search(self, retrieve):
        retrieve.return_value = {'results': []}
        self.assertEqual(self.registry.invoke('search_papers', {'query': '  topic  '}, context=self.local), {'results': []})
        retrieve.assert_called_once_with(1, 'topic', 'local')

    @patch('src.literature.retrieval.repository.local_search')
    @patch('src.literature.retrieval.repository.project_evidence')
    def test_collection_intersection_and_rank(self, evidence, search):
        evidence.return_value = [{'id': 2}, {'id': 3}, {'id': 100}]
        search.return_value = [{'id': 3}, {'id': 100}, {'id': 2}]
        result = self.registry.invoke('search_papers', {'query': 'topic'}, context=self.collection)
        self.assertEqual([p['id'] for p in result['results']], [3, 2])
        self.assertEqual(set(evidence.call_args.args[1]), {2, 3, 99})
        self.assertEqual(self.collection.paper_ids, (2, 3, 99))

    @patch('src.literature.evidence.repository.project_evidence')
    def test_local_and_collection_use_only_trusted_project_records(self, evidence):
        evidence.return_value = [{'id': 2, 'title': 'real', 'abstract': 'trusted', 'versions': []}]
        candidates = [{'id': 2, 'title': 'fake', 'abstract': 'forged'}, {'id': 99, 'title': 'outside', 'abstract': 'outside'}]
        for context in (self.local, self.collection):
            result = self.registry.invoke('collect_evidence', {'query': 'topic', 'papers': candidates}, context=context)
            self.assertEqual([(e['title'], e['text']) for e in result], [('real', 'trusted')])

    @patch('src.literature.evidence.repository.project_evidence', return_value=[])
    def test_external_candidate_cannot_claim_project_paper_id(self, evidence):
        context = ToolContext(1, 'all')
        result = self.registry.invoke('collect_evidence', {'query': 'topic', 'papers': [{'id': 99, 'title': 'external', 'abstract': 'text'}]}, context=context)
        self.assertIsNone(result[0]['paper_id'])

    @patch('src.tools.search.search_web')
    def test_web_search_and_evidence_independently(self, search):
        search.return_value = [{'title': 'page', 'url': 'https://EXAMPLE.org/a#fragment', 'content': 'x' * 1500,
                                'source_level': 'web_snippet', 'provider': 'mock', 'retrieved_at': 'today'}]
        items = self.registry.invoke('search_web', {'query': 'topic'}, context=self.web)
        result = self.registry.invoke('collect_evidence', {'query': 'topic', 'papers': items}, context=self.web)
        self.assertEqual(len(result[0]['text']), 1200)
        self.assertTrue(result[0]['id'].startswith('w'))
        self.assertEqual(result[0]['original_url'], items[0]['url'])
        duplicate = {**items[0], 'url': 'https://example.org/a#other'}
        self.assertEqual(len(self.registry.invoke('collect_evidence', {'query': 'topic', 'papers': [items[0], duplicate]}, context=self.web)), 1)
        for change in ({'source_level': 'full_text'}, {'url': 'https://user:pass@example.org/'}, {'url': 'http://[invalid'}):
            with self.assertRaises(ValueError):
                self.registry.invoke('collect_evidence', {'query': 'topic', 'papers': [{**items[0], **change}]}, context=self.web)


if __name__ == '__main__':
    unittest.main()
