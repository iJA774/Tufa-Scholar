import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.main import app
from src.persistence.storage import connect_database
from src.persistence.document_classifications import upsert_classification
from src.persistence.markdown_corpus import index_document


class ConversionCleanupTest(unittest.TestCase):
    def test_clear_preserves_originals_and_unrelated_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TUFA_DATA_DIR': tmp}), TestClient(app) as client:
            root = Path(tmp) / 'documents'
            original = root / 'originals' / '.store' / 'original.pdf'
            original.parent.mkdir(parents=True)
            original.write_bytes(b'original')
            md = root / 'conversions' / 'nested' / 'article.md'
            md.parent.mkdir(parents=True)
            md.write_text('# Article\nText', encoding='utf-8')
            with connect_database() as db:
                db.execute("INSERT INTO projects(name) VALUES ('keep')")
                project = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                db.execute("INSERT INTO settings(key,value) VALUES ('keep','value')")
                db.commit()
            upsert_classification('nested/article.md', 'abc', {'description': 'test', 'domains': ['field'], 'tags': ['tag']})
            index_document(project, 'nested/article.md', 'abc', '# Article\nText')
            self.assertEqual(client.post('/conversion-batches/clear', json={}).status_code, 422)
            response = client.post('/conversion-batches/clear', json={'confirm': True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(md.exists())
            self.assertEqual(original.read_bytes(), b'original')
            with connect_database() as db:
                for table in ('markdown_documents', 'markdown_chunks', 'markdown_fts', 'project_markdown', 'document_classifications', 'document_domains', 'document_tags'):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)
                self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT value FROM settings WHERE key='keep'").fetchone()[0], 'value')

    def test_active_batch_rejects_clear(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TUFA_DATA_DIR': tmp}), TestClient(app) as client:
            created = client.post('/conversion-batches', json={'root_name':'test', 'files':[{'path':'file.txt', 'size':4}]})
            self.assertEqual(created.status_code, 201)
            self.assertEqual(client.post('/conversion-batches/clear', json={'confirm': True}).status_code, 409)
    def test_failure_rolls_back_files_and_database(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TUFA_DATA_DIR': tmp}), TestClient(app) as client:
            root = Path(tmp) / 'documents' / 'conversions'
            first, second = root / 'a.md', root / 'b.md'
            first.write_text('a', encoding='utf-8')
            second.write_text('b', encoding='utf-8')
            upsert_classification('a.md', 'abc', {'description': 'test', 'domains': ['field'], 'tags': []})
            original_replace = Path.replace
            def fail_second(path, target):
                if path == second: raise OSError('injected')
                return original_replace(path, target)
            with patch.object(Path, 'replace', fail_second):
                response = client.post('/conversion-batches/clear', json={'confirm': True})
            self.assertEqual(response.status_code, 507)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            with connect_database() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM document_classifications').fetchone()[0], 1)

    def test_requires_boolean_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TUFA_DATA_DIR': tmp}), TestClient(app) as client:
            self.assertEqual(client.post('/conversion-batches/clear', json={'confirm': 1}).status_code, 422)
            self.assertEqual(client.post('/conversion-batches/clear', content='{"confirm":true}', headers={'content-type':'text/plain'}).status_code, 422)
    def test_running_local_research_blocks_clear(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TUFA_DATA_DIR': tmp}), TestClient(app) as client:
            with connect_database() as db:
                db.execute("INSERT INTO projects(name) VALUES ('project')")
                db.execute("INSERT INTO tasks(project_id, query, source, status, stage, kind, research_mode, local_literature) VALUES (1, 'q', 'local', 'running', 'searching', 'chat', 'deep', 1)")
                db.commit()
            self.assertEqual(client.post('/conversion-batches/clear', json={'confirm': True}).status_code, 409)
            with connect_database() as db:
                db.execute("UPDATE tasks SET research_mode='quick'")
                db.commit()
            self.assertEqual(client.post('/conversion-batches/clear', json={'confirm': True}).status_code, 200)
