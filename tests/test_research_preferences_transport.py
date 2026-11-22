import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.main import app
from src.persistence import repository


class ResearchPreferenceTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def test_preferences_are_validated_snapshotted_exposed_and_retried(self):
        project = self.client.post('/projects', json={'name': 'preferences'}).json()['id']
        response = self.client.post(f'/projects/{project}/questions', json={
            'question': 'compare research systems',
            'research_depth': 'deep',
            'prefer_primary_evidence': False,
            'relevance_filter': 'strict',
            'network_search': False,
            'local_literature': False,
        })
        self.assertEqual(response.status_code, 201)
        run_id = response.json()['run_id']

        task = repository.get_task(run_id)
        expected = {
            'research_depth': 'deep',
            'prefer_primary_evidence': False,
            'relevance_filter': 'strict',
            'network_search': False,
            'local_literature': False,
        }
        self.assertEqual({key: task[key] for key in expected}, expected)
        self.assertEqual({key: self.client.get(f'/runs/{run_id}').json()[key] for key in expected}, expected)

        repository.claim_task()
        repository.fail_task(run_id, 'retry')
        retried = self.client.post(f'/runs/{run_id}/retry').json()
        self.assertEqual({key: retried[key] for key in expected}, expected)
        claimed = repository.claim_task()
        self.assertEqual({key: claimed[key] for key in expected}, expected)

    def test_preferences_default_and_reject_invalid_values(self):
        project = self.client.post('/projects', json={'name': 'defaults'}).json()['id']
        created = self.client.post(f'/projects/{project}/questions', json={'question': 'q'})
        self.assertEqual(created.status_code, 201)
        task = repository.get_task(created.json()['run_id'])
        self.assertEqual(task['research_depth'], 'standard')
        self.assertIs(task['prefer_primary_evidence'], True)
        self.assertEqual(task['relevance_filter'], 'standard')
        self.assertIs(task['network_search'], True)
        self.assertIs(task['local_literature'], True)

        invalid = [
            {'research_depth': '3'},
            {'prefer_primary_evidence': 'false'},
            {'relevance_filter': 'off'},
            {'network_search': 'false'},
            {'local_literature': 'false'},
        ]
        for preference in invalid:
            with self.subTest(preference=preference):
                response = self.client.post(f'/projects/{project}/questions', json={'question': 'q', **preference})
                self.assertEqual(response.status_code, 422)

    def test_worker_receives_the_saved_preference_snapshot(self):
        from apps.worker.__main__ import run_once

        project = self.client.post('/projects', json={'name': 'worker snapshot'}).json()['id']
        response = self.client.post(f'/projects/{project}/questions', json={
            'question': 'q', 'research_depth': 'light',
            'prefer_primary_evidence': False, 'relevance_filter': 'relaxed',
            'network_search': False, 'local_literature': False,
        })
        self.assertEqual(response.status_code, 201)
        received = []

        def agent(research, *, history, on_stage, on_subtask, on_event, on_report_snapshot=None, on_detail=None):
            received.append(research)
            return 'answer', []

        with patch('src.agents.literature.run', side_effect=agent):
            self.assertTrue(run_once())
        self.assertEqual({key: received[0][key] for key in (
            'research_depth', 'prefer_primary_evidence', 'relevance_filter',
        )}, {
            'research_depth': 'light',
            'prefer_primary_evidence': False,
            'relevance_filter': 'relaxed',
        })
        self.assertFalse(received[0]['network_search'])
        self.assertFalse(received[0]['local_literature'])
        self.assertNotIn('local_papers', received[0])


if __name__ == '__main__':
    unittest.main()
