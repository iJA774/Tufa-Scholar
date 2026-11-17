import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.main import app
from src.persistence import repository as repo


class RegenerateApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.directory.name}, clear=True)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post('/projects', json={'name': 'regeneration'}).json()['id']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.directory.cleanup()

    def question(self, **kwargs):
        response = self.client.post(f'/projects/{self.project}/questions', json={
            'question': 'Original question', 'source': 'local', 'model_name': 'old-model',
            'research_mode': 'deep', 'research_depth': 'light',
            'prefer_primary_evidence': False, 'relevance_filter': 'strict', **kwargs,
        })
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_degraded_report_can_be_answered_again_without_losing_history(self):
        original = self.question(network_search=False, local_literature=False)
        repo.claim_task()
        repo.record_research_event(original['run_id'], 'report.fallback', {'message': 'Model unavailable'})
        old_report = repo.finish_chat_task(original['run_id'], 'Preserved draft', [])
        self.client.put('/settings', json={'openai_model': 'new-default'})

        response = self.client.post(f"/runs/{original['run_id']}/regenerate")
        self.assertEqual(response.status_code, 201)
        new = response.json()
        self.assertEqual(new['model_name'], 'new-default')
        self.assertEqual(new['origin_run_id'], original['run_id'])
        self.assertEqual(new['conversation_id'], original['conversation_id'])
        self.assertEqual(new['project_id'], self.project)
        self.assertEqual(new['query'], 'Original question')
        self.assertEqual(new['source'], 'local')
        self.assertFalse(new['network_search'])
        self.assertFalse(new['local_literature'])
        self.assertEqual((new['research_mode'], new['research_depth'], new['prefer_primary_evidence'], new['relevance_filter']),
                         ('deep', 'light', False, 'strict'))
        self.assertEqual(repo.task_history(new['id']), [{'role': 'user', 'content': 'Original question'}])
        self.assertEqual(repo.research_event_batch(new['id'], 0)[1], [])
        self.assertEqual(self.client.post(f"/runs/{original['run_id']}/regenerate").status_code, 409)
        self.assertEqual(repo.claim_task()['id'], new['id'])
        repo.finish_chat_task(new['id'], 'New answer', [])
        messages = self.client.get(f"/conversations/{original['conversation_id']}/messages").json()
        self.assertEqual([(m['role'], m['content']) for m in messages],
                         [('user', 'Original question'), ('assistant', 'Preserved draft'), ('assistant', 'New answer')])
        self.assertEqual(self.client.get(f"/reports/{old_report['report_id']}/markdown").status_code, 200)
        self.assertEqual(repo.get_task(original['run_id'])['model_name'], 'old-model')

    def test_current_model_selection_and_environment_precedence(self):
        original = self.question(research_mode='quick')
        repo.claim_task()
        repo.fail_task(original['run_id'], 'Old model unavailable')
        selected = self.client.post(f"/runs/{original['run_id']}/regenerate", json={'model_name': ' selected-model '}).json()
        self.assertEqual(selected['model_name'], 'selected-model')
        self.assertEqual(selected['research_mode'], 'quick')
        repo.claim_task()
        repo.fail_task(selected['id'], 'Retry also failed')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'environment-model'}):
            new = self.client.post(f"/runs/{selected['id']}/regenerate", json={'model_name': 'ignored'}).json()
        self.assertEqual(new['model_name'], 'environment-model')
        self.assertEqual(new['origin_run_id'], original['run_id'])
        self.assertEqual(repo.task_history(new['id']), [{'role': 'user', 'content': 'Original question'}])

    def test_collection_membership_snapshot_survives_changes_and_deletion(self):
        c = self.client
        collection = c.post(f'/projects/{self.project}/collections', json={'name': 'selected'}).json()['id']
        first = c.post(f'/projects/{self.project}/papers', json={'title': 'first'}).json()['id']
        other = c.post(f'/projects/{self.project}/papers', json={'title': 'later'}).json()['id']
        path = f'/projects/{self.project}/collections/{collection}/papers'
        c.post(path, json={'paper_id': first})
        original = self.question(source='collection', collection_id=collection)
        repo.claim_task()
        repo.finish_chat_task(original['run_id'], 'First report', [])
        c.post(path, json={'paper_id': other})
        c.delete(f'/projects/{self.project}/collections/{collection}')
        new = c.post(f"/runs/{original['run_id']}/regenerate", json={'model_name': 'replacement'}).json()
        self.assertEqual(new['paper_ids'], [first])
        self.assertEqual(new['source'], 'collection')
        self.assertEqual(new['collection_id'], collection)

    def test_regeneration_keeps_original_context_and_excludes_old_answer_and_later_turns(self):
        first = self.question(question='Prior context')
        repo.claim_task()
        repo.finish_chat_task(first['run_id'], 'Prior answer', [])
        target = self.question(conversation_id=first['conversation_id'])
        repo.claim_task()
        repo.finish_chat_task(target['run_id'], 'Do not reuse this degraded answer', [])
        later = self.question(question='Later question', conversation_id=first['conversation_id'])
        repo.claim_task()
        repo.finish_chat_task(later['run_id'], 'Later answer', [])
        new = self.client.post(f"/runs/{target['run_id']}/regenerate").json()
        self.assertEqual([m['content'] for m in repo.task_history(new['id'])],
                         ['Prior context', 'Prior answer', 'Original question'])
        repo.claim_task()
        repo.finish_chat_task(new['id'], 'Replacement answer', [])
        repeated = self.client.post(f"/runs/{new['id']}/regenerate").json()
        self.assertEqual(repeated['origin_run_id'], target['run_id'])
        self.assertEqual([m['content'] for m in repo.task_history(repeated['id'])],
                         ['Prior context', 'Prior answer', 'Original question'])

    def test_rejects_active_non_chat_missing_and_invalid_requests(self):
        c = self.client
        original = self.question()
        path = f"/runs/{original['run_id']}/regenerate"
        self.assertEqual(c.post(path).status_code, 409)
        repo.claim_task()
        self.assertEqual(c.post(path).status_code, 409)
        repo.interrupt_running_tasks()
        self.assertEqual(c.post(path, json={'model_name': ''}).status_code, 422)
        self.assertEqual(c.post(path, json={'model_name': 'm' * 301}).status_code, 422)
        self.assertEqual(c.post(path).status_code, 201)
        self.assertEqual(c.post('/runs/999999/regenerate').status_code, 404)
        retrieval = repo.create_task(self.project, None, 'paper query', 'local')
        repo.finish_task(retrieval, [])
        self.assertEqual(c.post(f'/runs/{retrieval}/regenerate').status_code, 409)

    def test_blocks_regeneration_while_another_turn_is_active(self):
        original = self.question()
        repo.claim_task()
        repo.finish_chat_task(original['run_id'], 'Answer', [])
        self.question(question='Another pending question', conversation_id=original['conversation_id'])
        response = self.client.post(f"/runs/{original['run_id']}/regenerate")
        self.assertEqual(response.status_code, 409)
        self.assertIn('请等待', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
