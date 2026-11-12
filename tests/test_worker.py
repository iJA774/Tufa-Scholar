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
    def test_local_paper_context_contains_only_latest_filenames_in_scope(self):
        from apps.worker.__main__ import _local_papers

        rows = [
            {'id': 1, 'title': 'private title one', 'versions': [
                {'filename': 'old.pdf'}, {'filename': 'latest.pdf'},
            ]},
            {'id': 2, 'title': 'private title two', 'versions': [
                {'filename': 'shared.pdf'},
            ]},
            {'id': 3, 'title': 'no file', 'versions': []},
            {'id': 4, 'title': 'outside snapshot', 'versions': [
                {'filename': 'outside.pdf'},
            ]},
        ]
        with patch.object(repository, 'list_papers', return_value=rows):
            project_names = _local_papers(7, 'local', [])
            collection_names = _local_papers(7, 'collection', [2, 1, 4, 3])

        self.assertEqual(project_names, ['latest.pdf', 'shared.pdf', 'outside.pdf'])
        self.assertEqual(collection_names, ['shared.pdf', 'latest.pdf', 'outside.pdf'])
        self.assertNotIn('private title one', str(collection_names))
        self.assertNotIn('old.pdf', str(project_names))
        self.assertNotIn('no file', str(collection_names))

    def test_chat_history_is_loaded_and_stage_callback_persists(self):
        from apps.worker.__main__ import run_once
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}):
            initialize_storage()
            project = repository.create_project('worker agent')['id']
            first = repository.create_question(project, None, 'first question', 'local')
            observed = []
            def agent(research, *, history, on_stage, on_subtask):
                on_stage('dispatcher')
                on_subtask({'index': 1, 'source': 'paper', 'task': 'topic', 'status': 'no_evidence', 'findings': [], 'gap': '缺少材料', 'sources': []})
                on_subtask({'index': 1, 'source': 'paper', 'task': 'duplicate', 'status': 'no_evidence', 'findings': [], 'gap': '缺少材料', 'sources': []})
                observed.append((research, history, repository.get_task(current['run_id'])['stage']))
                return 'answer', []
            current = first
            with patch('src.agents.literature.run', side_effect=agent), patch.object(repository, 'task_history', wraps=repository.task_history) as load:
                self.assertTrue(run_once())
                self.assertEqual(load.call_count, 1)
            self.assertEqual(observed[0][2], 'dispatcher')
            self.assertEqual([m['content'] for m in observed[0][1]], ['first question'])
            self.assertEqual(set(observed[0][0]), {'query', 'project_id', 'source', 'paper_ids', 'model_name', 'reasoning_effort', 'local_papers'})
            self.assertEqual(repository.get_task(first['run_id'])['status'], 'succeeded')
            self.assertEqual([n['task'] for n in repository.get_task(first['run_id'])['subtasks']], ['topic'])
            from fastapi.testclient import TestClient
            from apps.api.main import app
            with TestClient(app) as client:
                self.assertEqual(client.get(f"/runs/{first['run_id']}").json()['subtasks'][0]['task'], 'topic')
            collection = repository.create_collection(project, 'snapshot')['id']
            current = repository.create_question(project, first['conversation_id'], 'collection question', 'collection', collection_id=collection)
            with patch('src.agents.literature.run', side_effect=agent), patch.object(repository, 'task_history', side_effect=AssertionError('collection loaded history')) as load:
                self.assertTrue(run_once())
                load.assert_not_called()
            self.assertEqual(observed[1][1:], ([], 'dispatcher'))
            self.assertEqual(observed[1][0]['paper_ids'], [])
            self.assertEqual(repository.get_task(current['run_id'])['status'], 'succeeded')

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
            def agent(research, *, history, on_stage, on_subtask):
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
