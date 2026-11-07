import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.literature.evidence import collect_evidence
from src.persistence import repository, storage


class PdfEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': os.path.abspath(self.tmp.name)})
        self.env.start()
        storage.initialize_storage()
        self.project = repository.create_project('project')['id']

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save(self, pages=None):
        version = {'filename': 'a.pdf', 'digest': 'a', 'path': 'missing.pdf', 'text': 'flat', 'text_status': 'text_available'}
        if pages is not None:
            version['pages'] = pages
        return repository.save_paper(self.project, {'title': 'A', 'abstract': 'fallback'}, version)

    def collect(self, paper, query='distinctive'):
        return collect_evidence(self.project, 'local', None, query, [paper])

    def test_indexed_late_page_and_scope(self):
        paper = self.save(['early'] * 25 + ['distinctive late'])
        with patch('src.literature.evidence.PdfReader', side_effect=AssertionError('reparse')):
            result = self.collect(paper)
            self.assertEqual(result[0]['page'], 26)
            self.assertEqual(result[0]['id'], f"p{paper['id']}v{paper['versions'][0]['id']}p26")
            other = repository.create_project('other')['id']
            self.assertEqual(collect_evidence(other, 'local', None, 'distinctive', [paper]), [])

    def test_legacy_backfill_and_missing_fallback(self):
        paper = self.save()
        with storage.connect_database() as db:
            db.execute('DROP TABLE paper_pages')
            db.execute('ALTER TABLE paper_versions DROP COLUMN pages_indexed')
            db.commit()
        storage.initialize_storage()
        storage.initialize_storage()
        with patch('src.literature.evidence.PdfReader') as reader:
            reader.return_value.pages = [Mock(extract_text=lambda: 'distinctive')]
            self.assertEqual(self.collect(paper)[0]['page'], 1)
            self.collect(paper)
            self.assertEqual(reader.call_count, 1)
        with storage.connect_database() as db:
            db.execute('DELETE FROM paper_pages')
            db.execute('UPDATE paper_versions SET pages_indexed=0')
            db.commit()
        self.assertEqual(self.collect(paper)[0]['source_level'], 'abstract')

    def test_overlap_and_chinese_terms(self):
        paper = self.save(['x' * 1196 + 'distinctive ' + '神经网络模型'])
        result = self.collect(paper)
        self.assertIn('distinctive', result[0]['text'])
        self.assertEqual(len({item['id'] for item in result}), len(result))
        self.assertIn('神经网络模型', self.collect(paper, '网络模型')[0]['text'])
        self.assertTrue(all(len(item['text']) <= 1200 for item in result))

    def test_no_text_legacy_is_not_reparsed(self):
        paper = self.save()
        with storage.connect_database() as db:
            db.execute("UPDATE paper_versions SET text_status='no_extractable_text'")
            db.commit()
        with patch('src.literature.evidence.PdfReader') as reader:
            self.assertEqual(self.collect(paper)[0]['source_level'], 'abstract')
            reader.assert_not_called()

    def test_pdf_upload_and_file_endpoint(self):
        from fastapi.testclient import TestClient
        from apps.api.main import app
        from scripts.verify_chat import make_pdf

        pdf = make_pdf()
        with TestClient(app) as client:
            response = client.post(f'/projects/{self.project}/papers/import',
                                   data={'title': 'Uploaded'},
                                   files={'file': ('fixture.pdf', pdf, 'application/pdf')})
            self.assertEqual(response.status_code, 201)
            paper = response.json()
            result = self.collect(paper, 'accuracy')
            self.assertEqual(result[0]['page'], 1)
            self.assertIn('accuracy', result[0]['text'])
            self.assertEqual(client.get(paper['versions'][0]['file_url']).content, pdf)


if __name__ == '__main__':
    unittest.main()
