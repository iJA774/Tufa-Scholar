import re
import tempfile
import unittest
from unittest.mock import patch
from src.persistence.storage import initialize_storage, connect_database
from src.persistence.document_classifications import upsert_classification
from src.persistence.markdown_corpus import index_document
from src.tools import ToolContext, ToolRegistry
from src.tools.local_markdown import list_local_domains


class MarkdownCorpusTest(unittest.TestCase):
    def test_scope_versions_and_retrieval(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            with connect_database() as db:
                db.execute("INSERT INTO projects VALUES (1,'one'),(2,'two')")
                db.commit()
            upsert_classification('x.md', 'abc', {'description':'old','domains':['math'],'tags':['tag']})
            doc = index_document(1, 'x.md', 'abc', '# Title\nThis is mathematics.')
            registry = ToolRegistry()
            context = ToolContext(1, 'local')
            self.assertEqual(list_local_domains(context), ['math'])
            self.assertEqual(registry.invoke('select_local_domains', {'domains':['math']}, context=context)['documents'][0]['document_id'], doc)
            for query in ('mathematics', 'missing', ''):
                evidence = registry.invoke('read_local_markdown', {'document_id':doc, 'query':query}, context=context)[0]
                self.assertRegex(evidence['id'], r'^[a-zA-Z0-9_-]+$')
                self.assertEqual(evidence['source_level'], 'local_markdown')
            with self.assertRaises(ValueError):
                registry.invoke('read_local_markdown', {'document_id':doc, 'query':'math'}, context=ToolContext(2, 'local'))
            with self.assertRaises(ValueError):
                registry.invoke('read_local_markdown', {'document_id':doc, 'query':'math'}, context=ToolContext(1, 'all', local_literature=False))
            self.assertNotIn('collect_evidence', [tool['name'] for tool in registry.list_tools(context)])
            raw = '---\r\ntitle: "accuracy"\r\nsource: "paper.pdf"\r\n---\r\n\r\n# Heading\r\nEvidence text\r\n'
            normalized = raw.replace('\r\n', '\n')
            upsert_classification('converted.md', 'rawhash', {'description':'converted','domains':['math'],'tags':[]})
            converted = index_document(1, 'converted.md', 'rawhash', raw)
            item = registry.invoke('read_local_markdown', {'document_id':converted, 'query':'Evidence'}, context=context)[0]
            self.assertEqual(item['title'], 'accuracy')
            self.assertEqual(item['text'], normalized[item['char_start']:item['char_end']])
            self.assertNotIn('paper.pdf', item['text'])
            self.assertGreaterEqual(item['char_start'], normalized.index('---', 4) + 3)
            upsert_classification('x.md', 'new', {'description':'new','domains':['physics'],'tags':[]})
            index_document(2, 'x.md', 'new', '# New\nPhysics')
            self.assertEqual(list_local_domains(context), ['math'])
            self.assertEqual(registry.invoke('select_local_domains', {'domains':['math']}, context=context)['documents'][0]['description'], 'old')

if __name__ == '__main__':
    unittest.main()
