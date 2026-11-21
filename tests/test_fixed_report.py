import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.main import app
from apps.worker.__main__ import run_once
from src.persistence import repository as repo
from src.agents.literature import replay_report


class FixedReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post('/projects', json={'name': 'test'}).json()['id']
        question = self.client.post(f'/projects/{self.project}/questions', json={'question': 'q'}).json()
        self.conversation = question['conversation_id']
        repo.claim_task()
        self.snapshot = {'model': 'original', 'prompts': dict.fromkeys(('academic','exploratory','brief','explainer'), 'system'),
                         'payload': {'question': 'q'}, 'working': [], 'allowed': None, 'caveats': [],
                         'research': {'query': 'q', 'project_id': self.project, 'source': 'auto', 'research_depth': 'standard'}}
        self.report = repo.finish_chat_task(question['run_id'], 'original', [], snapshot=self.snapshot, report_model='original')['report_id']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def test_variant_failure_retry_and_conversation_deletion(self):
        c = self.client
        self.assertEqual(c.post('/reports/999999/regenerate', json={}).status_code, 404)
        response = c.post(f'/reports/{self.report}/regenerate', json={'report_format': 'brief', 'model_name': 'chosen'})
        self.assertEqual(response.status_code, 201)
        task = response.json()
        self.assertEqual(task['report_source_id'], self.report)
        self.assertNotIn('snapshot', task)
        self.assertEqual(c.post(f'/reports/{self.report}/regenerate', json={}).status_code, 409)
        with patch('src.agents.literature.replay_report', side_effect=RuntimeError('failure')):
            run_once()
        retry = c.post(f"/runs/{task['id']}/retry").json()
        self.assertEqual((retry['report_source_id'], retry['report_format'], retry['model_name']), (self.report, 'brief', 'chosen'))
        def replay_result(*args, on_stage, **kwargs):
            on_stage('reporter')
            return 'variant', []
        with patch('src.agents.literature.replay_report', side_effect=replay_result) as replay:
            run_once()
        self.assertEqual(replay.call_args.args[0], self.snapshot)
        messages = c.get(f'/conversations/{self.conversation}/messages').json()
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'assistant'])
        self.assertEqual(messages[-1]['metadata']['report_original_model'], 'original')
        self.assertIn('决策简报', messages[-1]['content'])
        self.assertIn('chosen', messages[-1]['content'])
        self.assertEqual(c.get(f'/reports/{self.report}').json()['content'], 'original')
        self.assertEqual(c.delete(f'/conversations/{self.conversation}').status_code, 204)
        self.assertIsNone(repo.get_report_snapshot(self.report))

    def test_replay_checks_evidence_and_emits_node_events(self):
        events = []
        with patch('src.agents.literature.structured', return_value='Supported [[unknown]].\n\nUncited conclusion.'):
            content, evidence = replay_report(self.snapshot, 'brief', 'chosen', on_event=lambda kind, data: events.append((kind, data)))
        self.assertNotIn('unknown', content)
        self.assertEqual(evidence, [])
        self.assertEqual([event[0] for event in events if event[0].startswith('node.')], ['node.started', 'node.completed'])
