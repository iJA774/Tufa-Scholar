import io
import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from apps.api.main import app
from src.persistence import repository as repo

class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get('TUFA_DATA_DIR')
        os.environ['TUFA_DATA_DIR'] = self.tmp.name
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if self.previous is None: os.environ.pop('TUFA_DATA_DIR', None)
        else: os.environ['TUFA_DATA_DIR'] = self.previous
        self.tmp.cleanup()

    def test_import_search_and_task_history(self):
        client = self.client
        project = client.post('/projects', json={'name': 'test'}).json()['id']
        conv = client.post(f'/projects/{project}/conversations', json={'title': 'chat'}).json()['id']
        def pdf(width):
            writer = PdfWriter(); writer.add_blank_page(width=width, height=200)
            out = io.BytesIO(); writer.write(out); return out.getvalue()
        def upload(width):
            return client.post(f'/projects/{project}/papers/import', data={'title':'Quantum Paper','doi':'10.1000/ABC'}, files={'file':('a.pdf',pdf(width),'application/pdf')})
        one = upload(200).json(); duplicate = upload(200).json(); two = upload(210).json()
        self.assertTrue(duplicate['duplicate']); self.assertEqual(one['id'],two['id'])
        self.assertEqual(one['imported_text_status'], 'no_extractable_text')
        self.assertEqual(duplicate['imported_text_status'], 'no_extractable_text')
        self.assertEqual(len(two['versions']),2)
        self.assertEqual(two['versions'][0]['text_status'], 'no_extractable_text')
        self.assertEqual(client.get(two['versions'][0]['file_url']).content,pdf(200))
        self.assertEqual(repo.local_search(project,'Quantum')[0]['id'],one['id'])
        run = client.post(f'/projects/{project}/runs', json={'query':'Quantum','source':'local','conversation_id':conv}).json()['run_id']
        self.assertEqual(repo.claim_task()['id'],run)
        repo.fail_task(run,'failure')
        retried = client.post(f'/runs/{run}/retry').json()
        self.assertNotEqual(retried['id'],run)
        self.assertEqual(client.get(f'/runs/{run}').json()['status'],'failed')
        history = client.get(f'/projects/{project}/runs', params={'conversation_id':conv}).json()
        self.assertEqual([item['id'] for item in history], [retried['id'], run])
        self.assertEqual(client.get('/projects/999/papers').status_code,404)
        self.assertEqual(client.post(f'/projects/{project}/runs',json={'query':'x','source':'bad'}).status_code,422)

    def test_delete_project_preserves_shared_papers_and_rejects_running(self):
        c = self.client
        a = c.post('/projects', json={'name':'delete'}).json()['id']
        b = c.post('/projects', json={'name':'keep'}).json()['id']
        conversation = c.post(f'/projects/{a}/conversations', json={'title':'chat'}).json()['id']
        writer = PdfWriter(); writer.add_blank_page(width=200,height=200)
        stream = io.BytesIO(); writer.write(stream)
        def upload(project):
            return c.post(f'/projects/{project}/papers/import', data={'title':'Shared'}, files={'file':('shared.pdf', stream.getvalue(), 'application/pdf')}).json()
        paper = upload(a)
        self.assertEqual(upload(b)['id'], paper['id'])
        pending = c.post(f'/projects/{a}/runs', json={'query':'test','source':'local','conversation_id':conversation}).json()['run_id']
        self.assertEqual(repo.claim_task()['id'], pending)
        self.assertEqual(c.delete(f'/projects/{a}').status_code, 409)
        self.assertEqual(len(c.get(f'/projects/{a}/papers').json()), 1)
        repo.fail_task(pending, 'failed')
        queued = c.post(f'/projects/{a}/runs', json={'query':'later','source':'local'}).json()['run_id']
        response = c.delete(f'/projects/{a}')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        self.assertEqual(c.delete(f'/projects/{a}').status_code, 404)
        self.assertEqual(c.get(f'/runs/{queued}').status_code, 404)
        self.assertEqual(c.get(f'/runs/{pending}').status_code, 404)
        self.assertEqual(c.get(f'/projects/{b}/papers').json()[0]['id'], paper['id'])
        self.assertEqual(c.get(paper['versions'][0]['file_url']).content, stream.getvalue())
        from src.persistence.storage import connect_database
        with connect_database() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

if __name__ == '__main__': unittest.main()
