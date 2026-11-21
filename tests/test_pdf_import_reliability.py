import io
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from apps.api.main import app
from apps.api.pdf_import import publish_snapshot
from tests.conversion_fixtures import pdf_fixture
from tests import test_document_conversion as conversion_tests


class SnapshotTest(unittest.TestCase):
    def test_failed_publication_and_parallel_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            data = b'%PDF-test data'
            source = Path(directory) / 'source.pdf'
            source.write_bytes(data)
            digest = sha256(data).hexdigest()
            with patch('apps.api.pdf_import.os.replace', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    publish_snapshot(source, digest)
            root = Path(directory) / 'documents' / 'originals' / '.store'
            self.assertEqual(list(root.iterdir()), [])
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: publish_snapshot(source, digest), range(4)))
            self.assertEqual(paths[0].read_bytes(), data)
            paths[0].write_bytes(b'corrupt')
            self.assertEqual(publish_snapshot(source, digest).read_bytes(), data)
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_upload_repairs_duplicate_and_enforces_limits(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}), TestClient(app) as client:
            project = client.post('/projects', json={'name': 'test'}).json()['id']
            data = pdf_fixture()
            def upload(content):
                return client.post(f'/projects/{project}/papers/import', files={'file': ('x.pdf', content, 'application/pdf')})
            first = upload(data)
            self.assertEqual(first.status_code, 201, first.text)
            from src.persistence.storage import connect_database
            version_id = first.json()['versions'][0]['id']
            legacy = Path(directory) / 'originals' / 'old.pdf'
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_bytes(b'broken')
            with connect_database() as db:
                before = db.execute('SELECT paper_id, text, pages_indexed FROM paper_versions WHERE id=?', (version_id,)).fetchone()
                pages = db.execute('SELECT page, text FROM paper_pages WHERE version_id=?', (version_id,)).fetchall()
                db.execute('UPDATE paper_versions SET path=? WHERE id=?', (str(legacy), version_id)); db.commit()
            snapshot = Path(directory) / 'documents' / 'originals' / '.store' / f'{sha256(data).hexdigest()}.pdf'
            snapshot.write_bytes(b'broken')
            again = upload(data)
            self.assertEqual(again.status_code, 201, again.text)
            self.assertTrue(again.json()['duplicate'])
            self.assertEqual(snapshot.read_bytes(), data)
            self.assertEqual(client.get(f'/paper-versions/{version_id}/file').content, data)
            with connect_database() as db:
                self.assertEqual(db.execute('SELECT paper_id, text, pages_indexed FROM paper_versions WHERE id=?', (version_id,)).fetchone(), before)
                self.assertEqual(db.execute('SELECT page, text FROM paper_pages WHERE version_id=?', (version_id,)).fetchall(), pages)
            writer = PdfWriter()
            for _ in range(201): writer.add_blank_page(width=10, height=10)
            buffer = io.BytesIO(); writer.write(buffer)
            self.assertEqual(upload(buffer.getvalue()).status_code, 422)
            self.assertEqual(upload(b'%PDF-' + b'x' * (20 * 1024 * 1024)).status_code, 413)

    def test_timeout_terminates_parser_and_frees_slot(self):
        from types import SimpleNamespace
        from fastapi import HTTPException
        from apps.api.pdf_import import parse_upload
        class HungProcess:
            pid = 1
            alive = True
            terminated = False
            def start(self): pass
            def join(self, timeout=None): pass
            def is_alive(self): return self.alive
            def terminate(self): self.terminated = True; self.alive = False
            def close(self): pass
        process = HungProcess()
        context = SimpleNamespace(Process=lambda **kwargs: process)
        with patch('apps.api.pdf_import.multiprocessing.get_context', return_value=context):
            with self.assertRaises(HTTPException) as error:
                parse_upload(SimpleNamespace(file=io.BytesIO(b'%PDF-test'), filename='test.pdf'))
        self.assertEqual(error.exception.status_code, 422)
        self.assertTrue(process.terminated)
        from apps.api.pdf_import import _upload_slot
        self.assertTrue(_upload_slot.acquire(blocking=False))
        _upload_slot.release()


class RetryImportTest(unittest.TestCase):
    setUp = conversion_tests.ConversionApiTest.setUp
    tearDown = conversion_tests.ConversionApiTest.tearDown
    finish = conversion_tests.ConversionApiTest.finish

    def test_existing_markdown_retries_import_and_repairs_snapshot(self):
        project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        root = Path(self.tmp.name) / 'documents'
        original = root / 'originals' / 'retry.pdf'
        original.parent.mkdir(parents=True, exist_ok=True)
        data = pdf_fixture(); original.write_bytes(data)
        markdown = root / 'conversions' / 'retry.pdf.md'
        markdown.parent.mkdir(parents=True, exist_ok=True)
        existing = '---\nsource_sha256: "' + sha256(data).hexdigest() + '"\n---\nExisting markdown'
        markdown.write_text(existing, encoding='utf-8')
        response = self.client.post('/conversion-batches/from-originals', json={'project_id': project})
        self.assertEqual(response.status_code, 201, response.text)
        batch = self.finish(response.json())
        self.assertIsNotNone(batch['files'][0].get('paper_id'), batch)
        self.assertEqual(markdown.read_text(encoding='utf-8'), existing)
        snapshot = root / 'originals' / '.store' / f'{sha256(data).hexdigest()}.pdf'
        from src.persistence.storage import connect_database
        legacy = Path(self.tmp.name) / 'legacy.pdf'
        legacy.write_bytes(b'broken')
        with connect_database() as db:
            version_id = db.execute('SELECT id FROM paper_versions WHERE digest=?', (sha256(data).hexdigest(),)).fetchone()[0]
            db.execute('UPDATE paper_versions SET path=? WHERE id=?', (str(legacy), version_id)); db.commit()
        snapshot.write_bytes(b'broken')
        response = self.client.post('/conversion-batches/from-originals', json={'project_id': project})
        self.assertEqual(response.status_code, 201, response.text)
        self.finish(response.json())
        self.assertEqual(snapshot.read_bytes(), data)
        self.assertEqual(self.client.get(f'/paper-versions/{version_id}/file').content, data)

    def test_retry_rejects_same_size_source_change_between_reads(self):
        from apps.api.pdf_import import markdown_source_matches
        project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        root = Path(self.tmp.name) / 'documents'
        original = root / 'originals' / 'race.pdf'
        original.parent.mkdir(parents=True, exist_ok=True)
        data = pdf_fixture(); original.write_bytes(data)
        markdown = root / 'conversions' / 'race.pdf.md'
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text('---\nsource_sha256: "' + sha256(data).hexdigest() + '"\n---\ntext', encoding='utf-8')
        def change_after_match(header, digest):
            matches = markdown_source_matches(header, digest)
            original.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            return matches
        with patch('apps.api.pdf_import.markdown_source_matches', side_effect=change_after_match):
            response = self.client.post('/conversion-batches/from-originals', json={'project_id': project})
        self.assertEqual(response.status_code, 201, response.text)
        batch = self.finish(response.json())
        self.assertEqual(batch['files'][0]['code'], 'read_failed')
        self.assertEqual(self.client.get(f'/projects/{project}/papers').json(), [])

    def test_long_chinese_markdown_does_not_fail_at_byte_boundary(self):
        project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        root = Path(self.tmp.name) / 'documents'
        original = root / 'originals' / 'chinese.pdf'
        original.parent.mkdir(parents=True, exist_ok=True)
        data = pdf_fixture(); original.write_bytes(data)
        markdown = root / 'conversions' / 'chinese.pdf.md'
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text('---\nsource_sha256: "' + sha256(data).hexdigest() + '"\n---\n' + '中' * 9000, encoding='utf-8')
        for _ in range(2):
            response = self.client.post('/conversion-batches/from-originals', json={'project_id': project})
            self.assertEqual(response.status_code, 201, response.text)
            batch = self.finish(response.json())
            self.assertIsNotNone(batch['files'][0].get('paper_id'), batch)

    def test_unrelated_markdown_does_not_import_pdf(self):
        project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        root = Path(self.tmp.name) / 'documents'
        original = root / 'originals' / 'paper.pdf'
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(pdf_fixture())
        markdown = root / 'conversions' / 'paper.pdf.md'
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text('---\nsource_sha256: "' + '0' * 64 + '"\n---\nold text', encoding='utf-8')
        response = self.client.post('/conversion-batches/from-originals', json={'project_id': project})
        self.assertEqual(response.status_code, 201, response.text)
        batch = self.finish(response.json())
        self.assertEqual(batch['files'][0]['code'], 'already_converted')
        self.assertIsNone(batch['files'][0].get('paper_id'))
        self.assertEqual(self.client.get(f'/projects/{project}/papers').json(), [])

    def test_markdown_index_uses_markdown_digest_when_pdf_does_not_match(self):
        from src.persistence.storage import connect_database
        project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        source = Path(self.tmp.name) / 'documents' / 'originals' / 'paper.pdf'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(pdf_fixture())
        markdown = '---\nsource_sha256: "' + '0' * 64 + '"\n---\nIndependent text'
        digest = sha256(markdown.encode()).hexdigest()
        runner = app.state.conversions
        with patch('apps.api.conversion_runner.store.get', return_value={'files': []}):
            runner.index_markdown({'kind': 'combined', 'project_id': project, 'id': 'test'},
                                  {'path': 'paper.pdf.md'}, digest, markdown)
        with connect_database() as db:
            row = db.execute('SELECT d.digest, pm.paper_id FROM markdown_documents d JOIN project_markdown pm ON d.id=pm.document_id WHERE d.path=?', ('paper.pdf.md',)).fetchone()
        self.assertEqual(row[0], digest)
        self.assertIsNone(row[1])

    def test_markdown_index_repairs_legacy_version_path(self):
        from src.persistence.storage import connect_database
        project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        data = pdf_fixture()
        response = self.client.post(f'/projects/{project}/papers/import', files={'file': ('paper.pdf', data, 'application/pdf')})
        self.assertEqual(response.status_code, 201, response.text)
        version_id = response.json()['versions'][0]['id']
        source = Path(self.tmp.name) / 'documents' / 'originals' / 'paper.pdf'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        legacy = Path(self.tmp.name) / 'legacy.pdf'; legacy.write_bytes(b'broken')
        with connect_database() as db:
            db.execute('UPDATE paper_versions SET path=? WHERE id=?', (str(legacy), version_id)); db.commit()
        markdown = '---\nsource_sha256: "' + sha256(data).hexdigest() + '"\n---\ntext'
        with patch('apps.api.conversion_runner.store.get', return_value={'files': []}):
            app.state.conversions.index_markdown({'kind': 'combined', 'project_id': project, 'id': 'test'},
                {'path': 'paper.pdf.md'}, sha256(markdown.encode()).hexdigest(), markdown)
        self.assertEqual(self.client.get(f'/paper-versions/{version_id}/file').content, data)
