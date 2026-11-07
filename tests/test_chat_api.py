import io
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from apps.api.main import app
from src.persistence import repository as repo

class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR':self.tmp.name}, clear=True); self.env.start()
        self.client = TestClient(app); self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None); self.env.stop(); self.tmp.cleanup()
    def test_chat_reports_search_and_delete(self):
        c=self.client
        project=c.post('/projects',json={'name':'研究'}).json()['id']
        other=c.post('/projects',json={'name':'其他'}).json()['id']
        paper=c.post(f'/projects/{project}/papers',json={'title':'科研领域进展','abstract':'实验准确率为 92%'}).json()
        r=c.post(f'/projects/{project}/questions',json={'question':'探讨准确率','source':'local'}).json()
        self.assertEqual(c.get(f"/conversations/{r['conversation_id']}/messages").json()[0]['role'],'user')
        self.assertEqual(repo.claim_task()['id'],r['run_id'])
        evidence=[{'id':'a1','paper_id':paper['id'],'title':paper['title'],'source_level':'abstract','version_id':None,'page':None,'text':'实验准确率为 92%'}]
        result=repo.finish_chat_task(r['run_id'],'准确率为 92%',evidence)
        self.assertEqual(len(c.get(f"/conversations/{r['conversation_id']}/messages").json()),2)
        rid=result['report_id']
        self.assertEqual(c.get(f'/reports/{rid}').json()['evidence'],evidence)
        self.assertIn('92%',c.get(f'/reports/{rid}/markdown').text)
        self.assertIn('a1',c.get(f'/reports/{rid}/evidence.csv').text)
        self.assertEqual(c.get(f'/projects/{project}/search',params={'q':'领域'}).json()['papers'][0]['id'],paper['id'])
        self.assertEqual(c.get(f'/projects/{other}/search',params={'q':'领域'}).json()['papers'],[])
        self.assertEqual(c.delete(f'/projects/{project}').status_code,204)
        self.assertEqual(c.get(f'/reports/{rid}').status_code,404)
        self.assertEqual(c.get(f"/conversations/{r['conversation_id']}/messages").status_code,404)
    def test_retry_preserves_one_user_message(self):
        c=self.client; p=c.post('/projects',json={'name':'retry'}).json()['id']
        run=c.post(f'/projects/{p}/questions',json={'question':'问题'}).json()
        repo.claim_task(); repo.fail_task(run['run_id'],'failure')
        retry=c.post(f"/runs/{run['run_id']}/retry").json()
        self.assertEqual(retry['kind'],'chat'); self.assertEqual(repo.claim_task()['id'],retry['id'])
        repo.finish_chat_task(retry['id'],'回答',[])
        self.assertEqual([m['role'] for m in c.get(f"/conversations/{run['conversation_id']}/messages").json()],['user','assistant'])

    def test_retry_keeps_original_history_boundary(self):
        c = self.client
        p = c.post('/projects', json={'name':'history'}).json()['id']
        first = c.post(f'/projects/{p}/questions', json={'question':'原问题'}).json()
        repo.claim_task(); repo.fail_task(first['run_id'], 'failure')
        c.post(f'/projects/{p}/questions', json={'question':'后来的问题', 'conversation_id':first['conversation_id']})
        retry = c.post(f"/runs/{first['run_id']}/retry").json()
        self.assertEqual([m['content'] for m in repo.task_history(retry['id'])], ['原问题'])
        self.assertEqual(c.delete(f'/projects/{p}').status_code, 204)

if __name__=='__main__': unittest.main()
