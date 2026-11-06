import os, tempfile, unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from apps.api.main import app
from src.persistence import repository as repo
from src.persistence.storage import connect_database

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'TUFA_DATA_DIR':self.tmp.name},clear=True); self.env.start()
        self.c=TestClient(app); self.c.__enter__()
    def tearDown(self):
        self.c.__exit__(None,None,None); self.env.stop(); self.tmp.cleanup()
    def test_collection_scope_snapshot_retry_and_delete(self):
        c=self.c
        a=c.post('/projects',json={'name':'A'}).json()['id']; b=c.post('/projects',json={'name':'B'}).json()['id']
        ca=c.post(f'/projects/{a}/collections',json={'name':'selected'}).json()['id']
        cb=c.post(f'/projects/{b}/collections',json={'name':'other'}).json()['id']
        p=c.post(f'/projects/{a}/papers',json={'title':'local'}).json()
        path=f'/projects/{a}/collections/{ca}/papers'
        self.assertEqual(c.post(path,json={'paper_id':p['id']}).status_code,201)
        c.post(path,json={'paper_id':p['id']})
        external=c.post(path,json={'title':'remote','doi':'10.1/example'}).json()
        self.assertEqual(len(c.get(path).json()),2)
        self.assertEqual(c.post(f'/projects/{a}/collections/{cb}/papers',json={'title':'bad'}).status_code,404)
        self.assertEqual(c.post(f'/projects/{a}/questions',json={'question':'q','source':'collection','collection_id':cb}).status_code,404)
        self.assertEqual(c.post(f'/projects/{a}/questions',json={'question':'q','source':'local','collection_id':ca}).status_code,422)
        q=c.post(f'/projects/{a}/questions',json={'question':'q','source':'collection','collection_id':ca}).json()
        c.delete(path+'/'+str(external['id']))
        t=repo.claim_task(); self.assertEqual(set(t['paper_ids']),{p['id'],external['id']})
        self.assertEqual(set(x['id'] for x in repo.project_evidence(a,t['paper_ids'])),set(t['paper_ids']))
        with self.assertRaises(ValueError): repo.finish_chat_task(q['run_id'],'bad',[{'paper_id':9999}])
        repo.fail_task(q['run_id'],'failed')
        retried=c.post(f"/runs/{q['run_id']}/retry").json(); self.assertEqual(set(retried['paper_ids']),set(t['paper_ids']))
        repo.claim_task(); repo.finish_chat_task(retried['id'],'ok',[])
        self.assertEqual(c.delete(f'/projects/{a}').status_code,204)
        with connect_database() as db: self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_delete_collection_keeps_papers_and_task_snapshot(self):
        c=self.c
        a=c.post('/projects',json={'name':'A'}).json()['id']
        b=c.post('/projects',json={'name':'B'}).json()['id']
        one=c.post(f'/projects/{a}/collections',json={'name':'one'}).json()['id']
        two=c.post(f'/projects/{a}/collections',json={'name':'two'}).json()['id']
        paper=c.post(f'/projects/{a}/papers',json={'title':'shared'}).json()['id']
        for collection in (one,two):
            self.assertEqual(c.post(f'/projects/{a}/collections/{collection}/papers',json={'paper_id':paper}).status_code,201)
        task=c.post(f'/projects/{a}/questions',json={'question':'test','source':'collection','collection_id':one}).json()['run_id']
        self.assertEqual(c.delete(f'/projects/{b}/collections/{one}').status_code,404)
        self.assertEqual(c.delete(f'/projects/{a}/collections/{one}').status_code,204)
        self.assertEqual(c.delete(f'/projects/{a}/collections/{one}').status_code,404)
        self.assertEqual(c.get(f'/projects/{a}/papers').json()[0]['id'],paper)
        self.assertEqual(c.get(f'/projects/{a}/collections/{two}/papers').json()[0]['id'],paper)
        claimed=repo.claim_task(); self.assertEqual(claimed['paper_ids'],[paper])
        repo.fail_task(task,'retry')
        retried=c.post(f'/runs/{task}/retry').json()
        self.assertEqual(retried['paper_ids'],[paper])
        self.assertEqual(retried['collection_id'],one)
        with connect_database() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__': unittest.main()
