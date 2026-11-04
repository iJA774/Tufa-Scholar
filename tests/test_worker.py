import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import repository
from src.storage import initialize_storage


class WorkerTests(unittest.TestCase):
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
