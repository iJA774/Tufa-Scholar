import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.persistence import repository
from src.persistence.storage import initialize_storage


class WorkerTests(unittest.TestCase):
    def test_chat_history_is_loaded_and_stage_callback_persists(self):
        from apps.worker.__main__ import run_once
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('worker agent')['id']
            first = repository.create_question(project, None, 'first question', 'local')
            observed = []
            def agent(research, *, history, on_stage, on_subtask, on_event, on_report_snapshot=None, on_detail=None):
                on_stage('dispatcher')
                on_event('node.started', {'node': 'planner'})
                on_subtask({'index': 1, 'source': 'paper', 'task': 'topic', 'status': 'no_evidence', 'findings': [], 'gap': '缺少材料', 'sources': []})
                on_subtask({'index': 1, 'source': 'paper', 'task': 'duplicate', 'status': 'no_evidence', 'findings': [], 'gap': '缺少材料', 'sources': []})
                observed.append((research, history, repository.get_task(current['run_id'])['stage']))
                return 'answer', []
            current = first
            with patch('src.agents.literature.run', side_effect=agent), patch.object(repository, 'task_history', wraps=repository.task_history) as load, patch.object(repository, 'list_papers') as list_papers:
                self.assertTrue(run_once())
                self.assertEqual(load.call_count, 1)
                list_papers.assert_not_called()
            self.assertEqual(observed[0][2], 'dispatcher')
            self.assertEqual([m['content'] for m in observed[0][1]], ['first question'])
            self.assertEqual(set(observed[0][0]), {'query', 'project_id', 'source', 'paper_ids', 'model_name', 'reasoning_effort', 'research_mode', 'report_format', 'research_depth', 'prefer_primary_evidence', 'relevance_filter', 'network_search', 'local_literature'})
            self.assertEqual(observed[0][0]['research_mode'], 'auto')
            self.assertEqual(repository.get_task(first['run_id'])['status'], 'succeeded')
            self.assertEqual([n['task'] for n in repository.get_task(first['run_id'])['subtasks']], ['topic'])
            from fastapi.testclient import TestClient
            from apps.api.main import app
            with TestClient(app) as client:
                self.assertEqual(client.get(f"/runs/{first['run_id']}").json()['subtasks'][0]['task'], 'topic')
                event_path = f"/runs/{first['run_id']}/events"
                response = client.get(event_path)
                self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                self.assertEqual([(event['kind'], event['data']) for event in events], [('node.started', {'node': 'planner'})])
                self.assertNotIn('data: ', client.get(event_path, headers={'Last-Event-ID': str(events[0]['id'])}).text)
                self.assertEqual(client.get(event_path, params={'after': -1}).status_code, 422)
                self.assertEqual(client.get('/runs/999999/events').status_code, 404)
            collection = repository.create_collection(project, 'snapshot')['id']
            current = repository.create_question(project, first['conversation_id'], 'collection question', 'collection', collection_id=collection)
            with patch('src.agents.literature.run', side_effect=agent), patch.object(repository, 'task_history', side_effect=AssertionError('collection loaded history')) as load:
                self.assertTrue(run_once())
                load.assert_not_called()
            self.assertEqual(observed[1][1:], ([], 'dispatcher'))
            self.assertEqual(observed[1][0]['paper_ids'], [])
            self.assertEqual(repository.get_task(current['run_id'])['status'], 'succeeded')

    def test_worker_passes_saved_research_mode_to_graph(self):
        from apps.worker.__main__ import run_once
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('mode passthrough')['id']
            task = repository.create_question(project, None, 'question', 'local', research_mode='deep', report_format='brief')
            received = []

            def agent(research, *, history, on_stage, on_subtask, on_event, on_report_snapshot=None, on_detail=None):
                received.append((research, history))
                return 'answer', []

            with patch('src.agents.literature.run', side_effect=agent):
                self.assertTrue(run_once())
            self.assertEqual(received[0][0]['research_mode'], 'deep')
            self.assertEqual(received[0][0]['report_format'], 'brief')
            self.assertEqual(repository.get_task(task['run_id'])['research_mode'], 'deep')

    def test_research_detail_payload_strips_credentials_and_private_response_text(self):
        from apps.worker.__main__ import run_once

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'TUFA_DATA_DIR': directory, 'OPENAI_API_KEY': 'fixture-model-credential',
        }, clear=True):
            initialize_storage()
            project = repository.create_project('detail scope')['id']
            run_id = repository.create_question(project, None, 'question', 'local')['run_id']
            self.assertEqual(repository.research_detail_batch(run_id, 0)['entries'], [])

            def agent(research, *, on_detail, **kwargs):
                on_detail({
                    'node': 'reporter', 'node_id': 'node-1', 'call_id': 'call-1',
                    'attempt': 1, 'phase': None, 'kind': 'model', 'direction': 'output', 'name': 'reporter',
                }, {
                    'authorization': 'header-value',
                    'content': '<think>provider-thought</think> visible fixture-model-credential',
                    'source': 'https://example.org/material?token=url-value',
                })
                return 'answer', []

            with patch('src.agents.literature.run', side_effect=agent):
                self.assertTrue(run_once())
            entries = repository.research_detail_batch(run_id, 0)['entries']
            self.assertEqual(len(entries), 1)
            data = repository.get_research_detail(run_id, entries[0]['id'])
            self.assertEqual(data['value'], {
                'content': ' visible [redacted]', 'source': 'https://example.org/material?[redacted]',
            })

    def test_subtasks_survive_failure_and_retry_starts_empty(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('notes')['id']
            run_id = repository.create_question(project, None, 'question', 'local')['run_id']
            note = {'index': 2, 'source': 'local', 'task': 'study', 'status': 'sources_only', 'findings': [], 'sources': [{'id': 'a', 'title': 'paper', 'source_level': 'abstract'}]}
            repository.record_subtask(run_id, note)
            self.assertEqual(repository.get_task(run_id)['subtasks'], [])
            repository.claim_task()
            repository.record_subtask(run_id, note)
            repository.record_subtask(run_id, {**note, 'task': 'duplicate'})
            repository.fail_task(run_id, 'failed')
            self.assertEqual(repository.get_task(run_id)['subtasks'], [note])
            retried = repository.retry_task(run_id)
            self.assertEqual(retried['subtasks'], [])
            repository.record_subtask(run_id, {**note, 'index': 3})
            self.assertEqual(repository.get_task(run_id)['subtasks'], [note])

    def test_diagnostic_scope_tracks_task_and_resets_after_exception(self):
        from apps.worker.__main__ import run_once
        from src.infrastructure.diagnostics import record_failure
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('scope')['id']
            task = repository.create_question(project, None, 'question', 'local')
            ids = []
            def agent(research, *, history, on_stage, on_subtask, on_event, on_report_snapshot=None, on_detail=None):
                self.assertNotIn('id', research)
                ids.append(record_failure(kind='test', payload={}, schema={}))
                raise RuntimeError('failure')
            with patch('src.agents.literature.run', side_effect=agent):
                self.assertTrue(run_once())
            ids.append(record_failure(kind='test', payload={}, schema={}))
            data = [json.loads((Path(directory) / 'diagnostics' / (i + '.json')).read_text(encoding='utf-8')) for i in ids]
            self.assertEqual(data[0]['context'], {'run_id': task['run_id'], 'project_id': project})
            self.assertEqual(data[1]['context'], {})
            self.assertEqual(repository.get_task(task['run_id'])['status'], 'failed')
            status, events = repository.research_event_batch(task['run_id'], 0)
            self.assertEqual(status, 'failed')
            failures = [event for event in events if event['kind'] == 'task.failed']
            self.assertEqual(len(failures), 1)
            self.assertTrue(failures[0]['data']['suggestion'])
            self.assertNotIn('failure', str(failures))
            self.assertEqual(repository.research_event_batch(task['run_id'], failures[0]['id'])[1], [])

    def test_model_failure_categories_are_safe_and_replayed(self):
        from apps.worker.__main__ import run_once
        from src.integrations.model import ModelServiceError
        for category, expected in [('rate_limit', '限流'), ('auth', '权限'),
                                   ('not_found', '地址或模型'), ('timeout', '超时')]:
            with self.subTest(category=category), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
                initialize_storage()
                project = repository.create_project('failure')['id']
                run_id = repository.create_question(project, None, 'question', 'local')['run_id']
                with patch('src.agents.literature.run', side_effect=ModelServiceError('secret', category=category)):
                    run_once()
                status, events = repository.research_event_batch(run_id, 0)
                self.assertEqual(status, 'failed')
                self.assertEqual(len(events), 1)
                self.assertIn(expected, events[0]['data']['message'])
                self.assertTrue(events[0]['data']['suggestion'])
                self.assertNotIn('secret', str(events))

    def test_local_ranking_via_actual_worker_entry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('检索验收')
            conversation = repository.create_conversation(project['id'], '固定问题')
            repository.save_paper(project['id'], {'title': 'Attention Is All You Need', 'doi': '10.48550/arxiv.1706.03762'})
            repository.save_paper(project['id'], {'title': 'Attention mechanisms in neural networks'})
            repository.save_paper(project['id'], {'title': 'A study of language models'})
            task = repository.create_task(project['id'], conversation['id'], 'Attention Is All You Need', 'local')
            subprocess.run([sys.executable, '-m', 'apps.worker', '--once'], check=True, env=os.environ.copy())
            result = repository.get_task(task)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['result']['results'][0]['title'], 'Attention Is All You Need')
            self.assertEqual(result['result']['results'][0]['rank'], 1)
            self.assertTrue(all(r['retrieval_sources'][0]['source'] == 'local' for r in result['result']['results']))

    def test_restart_marks_running_interrupted(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('恢复验收')
            conversation = repository.create_conversation(project['id'], '任务')
            task = repository.create_task(project['id'], conversation['id'], 'attention', 'local')
            repository.claim_task()
            subprocess.run([sys.executable, '-m', 'apps.worker', '--once'], check=True, env=os.environ.copy())
            self.assertEqual(repository.get_task(task)['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
