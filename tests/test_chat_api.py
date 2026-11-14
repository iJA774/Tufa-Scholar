import io
import os
import sqlite3
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
        self.assertEqual(repo.get_task(run['run_id'])['source'], 'auto')
        repo.claim_task(); repo.fail_task(run['run_id'],'failure')
        retry=c.post(f"/runs/{run['run_id']}/retry").json()
        self.assertEqual(retry['source'], 'auto')
        self.assertEqual(retry['research_mode'], 'auto')
        self.assertEqual(retry['kind'],'chat'); self.assertEqual(repo.claim_task()['id'],retry['id'])
        repo.finish_chat_task(retry['id'],'回答',[])
        self.assertEqual([m['role'] for m in c.get(f"/conversations/{run['conversation_id']}/messages").json()],['user','assistant'])

    def test_auto_does_not_relax_collection_or_search_scope(self):
        c = self.client
        project = c.post('/projects', json={'name': 'scope'}).json()['id']
        collection = c.post(f'/projects/{project}/collections', json={'name': 'selected'}).json()['id']
        self.assertEqual(c.post(f'/projects/{project}/questions', json={'question': 'q', 'source': 'collection'}).status_code, 422)
        self.assertEqual(c.post(f'/projects/{project}/questions', json={'question': 'q', 'collection_id': collection}).status_code, 422)
        self.assertEqual(c.get(f'/projects/{project}/paper-search', params={'q': 'topic', 'source': 'auto'}).status_code, 422)
        selected = c.post(f'/projects/{project}/questions', json={'question': 'q', 'source': 'collection', 'collection_id': collection})
        self.assertEqual(selected.status_code, 201)
        self.assertEqual(repo.get_task(selected.json()['run_id'])['source'], 'collection')
        self.assertEqual(repo.get_task(selected.json()['run_id'])['research_mode'], 'auto')
        quick = c.post(f'/projects/{project}/questions', json={
            'question': 'q', 'source': 'collection', 'collection_id': collection, 'research_mode': 'quick',
        })
        self.assertEqual(quick.status_code, 201)
        self.assertEqual(repo.get_task(quick.json()['run_id'])['research_mode'], 'quick')

    def test_research_mode_validation_and_retry_persistence(self):
        c = self.client
        project = c.post('/projects', json={'name': 'research modes'}).json()['id']
        invalid = c.post(f'/projects/{project}/questions', json={
            'question': 'q', 'research_mode': 'fast',
        })
        self.assertEqual(invalid.status_code, 422)

        created = c.post(f'/projects/{project}/questions', json={
            'question': 'quick answer', 'research_mode': 'quick',
        })
        self.assertEqual(created.status_code, 201)
        task = repo.get_task(created.json()['run_id'])
        self.assertEqual(task['research_mode'], 'quick')
        self.assertEqual(c.get(f"/runs/{task['id']}").json()['research_mode'], 'quick')
        repo.claim_task()
        repo.fail_task(task['id'], 'failed')
        retry = c.post(f"/runs/{task['id']}/retry").json()
        self.assertEqual(retry['research_mode'], 'quick')
        self.assertEqual(repo.claim_task()['id'], retry['id'])

        old_client = c.post(f'/projects/{project}/questions', json={'question': 'legacy request'})
        self.assertEqual(old_client.status_code, 201)
        self.assertEqual(repo.get_task(old_client.json()['run_id'])['research_mode'], 'auto')

    def test_global_conversations_are_flat_sorted_and_keep_project_ids(self):
        c = self.client
        first_project = c.post('/projects', json={'name': 'project one'}).json()['id']
        second_project = c.post('/projects', json={'name': 'project two'}).json()['id']
        older = c.post(f'/projects/{first_project}/questions', json={'question': 'older'}).json()
        newer = c.post(f'/projects/{second_project}/questions', json={'question': 'newer'}).json()
        empty = c.post(f'/projects/{first_project}/conversations', json={'title': 'empty'}).json()

        from src.persistence.storage import connect_database
        with connect_database() as db:
            db.execute("UPDATE messages SET created_at='2026-11-10 09:00:00' WHERE run_id=?", (repo.get_task(older['run_id'])['id'],))
            db.execute("UPDATE messages SET created_at='2026-11-12 09:00:00' WHERE run_id=?", (repo.get_task(newer['run_id'])['id'],))
            db.commit()

        rows = c.get('/conversations').json()
        self.assertEqual([item['id'] for item in rows], [newer['conversation_id'], older['conversation_id'], empty['id']])
        self.assertEqual([item['project_id'] for item in rows], [second_project, first_project, first_project])
        self.assertEqual(rows[0]['updated_at'], '2026-11-12 09:00:00')
        self.assertIsNone(rows[-1]['updated_at'])
        self.assertTrue(all(set(item) == {'id', 'project_id', 'title', 'pinned', 'updated_at'} for item in rows))
        self.assertTrue(all(item['pinned'] is False for item in rows))
        self.assertEqual(c.get(f'/projects/{first_project}/conversations').status_code, 200)

    def test_conversation_pin_patch_updates_boolean_and_moves_it_ahead_of_recent_history(self):
        c = self.client
        project = c.post('/projects', json={'name': 'pinning'}).json()['id']
        older = c.post(f'/projects/{project}/questions', json={'question': 'older'}).json()
        newer = c.post(f'/projects/{project}/questions', json={'question': 'newer'}).json()

        response = c.patch(f"/conversations/{older['conversation_id']}", json={'pinned': True})
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated['id'], older['conversation_id'])
        self.assertEqual(updated['project_id'], project)
        self.assertTrue(updated['pinned'] is True)
        self.assertEqual(updated['title'], 'older')
        self.assertIsNotNone(updated['updated_at'])
        rows = c.get('/conversations').json()
        self.assertEqual([row['id'] for row in rows], [older['conversation_id'], newer['conversation_id']])
        self.assertEqual([row['pinned'] for row in rows], [True, False])

        unpinned = c.patch(f"/conversations/{older['conversation_id']}", json={'pinned': False})
        self.assertEqual(unpinned.status_code, 200)
        self.assertIs(unpinned.json()['pinned'], False)
        self.assertEqual(c.patch(f"/conversations/{older['conversation_id']}", json={'pinned': 'false'}).status_code, 422)
        self.assertEqual(c.patch('/conversations/999999', json={'pinned': True}).status_code, 404)

    def test_delete_conversation_rejects_pending_and_running_then_removes_only_its_history(self):
        c = self.client
        project = c.post('/projects', json={'name': 'delete conversation'}).json()['id']
        paper = c.post(f'/projects/{project}/papers', json={'title': 'keep this paper'}).json()
        target = c.post(f'/projects/{project}/conversations', json={'title': 'target'}).json()
        other = c.post(f'/projects/{project}/questions', json={'question': 'keep this conversation'}).json()
        run = c.post(f'/projects/{project}/questions', json={
            'question': 'target question', 'conversation_id': target['id'],
        }).json()

        self.assertEqual(c.delete(f"/conversations/{target['id']}").status_code, 409)
        claimed = repo.claim_task()
        self.assertEqual(claimed['id'], other['run_id'])
        repo.fail_task(claimed['id'], 'finished for this test')
        claimed = repo.claim_task()
        self.assertEqual(claimed['id'], run['run_id'])
        self.assertEqual(c.delete(f"/conversations/{target['id']}").status_code, 409)
        repo.fail_task(run['run_id'], 'retry this task')
        retry = c.post(f"/runs/{run['run_id']}/retry").json()
        self.assertEqual(retry['origin_run_id'], run['run_id'])
        self.assertEqual(c.delete(f"/conversations/{target['id']}").status_code, 409)
        claimed = repo.claim_task()
        self.assertEqual(claimed['id'], retry['id'])
        self.assertEqual(c.delete(f"/conversations/{target['id']}").status_code, 409)
        repo.finish_chat_task(retry['id'], 'target answer', [])

        from src.persistence.storage import connect_database
        with connect_database() as db:
            report_id = db.execute('SELECT id FROM reports WHERE conversation_id=?', (target['id'],)).fetchone()[0]
        self.assertEqual(c.delete(f"/conversations/{target['id']}").status_code, 204)
        self.assertEqual(c.delete(f"/conversations/{target['id']}").status_code, 404)
        self.assertEqual(c.patch(f"/conversations/{target['id']}", json={'pinned': True}).status_code, 404)
        self.assertEqual(c.get(f"/conversations/{other['conversation_id']}/messages").status_code, 200)
        self.assertEqual(c.get(f'/reports/{report_id}').status_code, 404)
        self.assertEqual(c.get(f'/projects/{project}/papers').json()[0]['id'], paper['id'])
        self.assertEqual(c.get('/projects').json()[0]['id'], project)
        with connect_database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks WHERE conversation_id=?', (target['id'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?', (target['id'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reports WHERE conversation_id=?', (target['id'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_existing_task_rows_migrate_to_auto_without_rebuilding_database(self):
        from src.persistence.storage import initialize_storage

        with tempfile.TemporaryDirectory() as legacy_dir:
            path = os.path.join(legacy_dir, 'research.db')
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE conversations (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), title TEXT NOT NULL);
                CREATE TABLE tasks (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), conversation_id INTEGER REFERENCES conversations(id), query TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, result TEXT, error TEXT);
                CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, run_id INTEGER REFERENCES tasks(id), metadata TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO projects VALUES (1, 'legacy');
                INSERT INTO conversations VALUES (1, 1, 'legacy conversation');
                INSERT INTO tasks (id, project_id, conversation_id, query, source, status, stage) VALUES (1, 1, 1, 'q', 'auto', 'pending', 'pending');
                INSERT INTO messages (id, conversation_id, role, content, run_id) VALUES (1, 1, 'user', 'legacy message', 1);
            """)
            db.close()

            with patch.dict(os.environ, {'TUFA_DATA_DIR': legacy_dir}):
                initialize_storage()
                initialize_storage()
                from src.persistence.storage import connect_database
                with connect_database() as migrated:
                    row = migrated.execute('SELECT query, research_mode FROM tasks WHERE id=1').fetchone()
                    self.assertEqual(tuple(row), ('q', 'auto'))
                    message = migrated.execute('SELECT content FROM messages WHERE id=1').fetchone()
                    self.assertEqual(message[0], 'legacy message')
                    conversation = migrated.execute('SELECT project_id, title, pinned FROM conversations WHERE id=1').fetchone()
                    self.assertEqual(tuple(conversation), (1, 'legacy conversation', 0))
                    migrated.execute("INSERT INTO tasks (project_id, query, source, status, stage, kind) VALUES (1, 'new', 'auto', 'pending', 'pending', 'chat')")
                    self.assertEqual(migrated.execute('SELECT research_mode FROM tasks WHERE query=?', ('new',)).fetchone()[0], 'auto')

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
