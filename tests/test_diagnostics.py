import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.replay_diagnostic import replay
from src.infrastructure.diagnostics import diagnostic_scope, record_failure


class DiagnosticsTest(unittest.TestCase):
    def test_record_redacts_private_values_and_keeps_task_scope(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            'src.infrastructure.diagnostics.data_directory', return_value=Path(tmp)
        ), patch('src.infrastructure.settings._local', return_value={'openai_api_key': 'private-value'}):
            with diagnostic_scope(run_id=42):
                ident = record_failure(
                    kind='model_validation',
                    payload={'api_key': 'hidden', 'nested': ['private-value'], 'private-value': 'x'},
                    schema={},
                    response='{"needs_research":"bad", "authorization":"hidden"}',
                    context={'prompt_id': 'runtime/dispatcher'},
                )
            path = Path(tmp) / 'diagnostics' / (ident + '.json')
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('private-value', text)
            self.assertNotIn('hidden', text)
            artifact = json.loads(text)
            self.assertEqual(artifact['context']['run_id'], 42)
            self.assertTrue(artifact['redacted'])
            self.assertIn('python', artifact['environment'])
            self.assertIn('复现', replay(artifact))

            env = {**os.environ, 'TUFA_DATA_DIR': str(Path(tmp) / 'must-not-exist'), 'PYTHONIOENCODING': 'utf-8'}
            result = subprocess.run(
                [sys.executable, '-B', '-c',
                 "import socket, sqlite3, runpy; socket.socket.connect = sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('forbidden')); runpy.run_module('scripts.replay_diagnostic', run_name='__main__')",
                 str(path)],
                capture_output=True, text=True, encoding='utf-8', env=env,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn('复现', result.stdout)
            self.assertFalse((Path(tmp) / 'must-not-exist').exists())

    def test_replay_uses_only_current_prompt_schemas(self):
        base = {'kind': 'model_validation', 'context': {'prompt_id': 'runtime/dispatcher'}}
        self.assertTrue(replay({**base, 'response': '{}'}).startswith('复现：'))
        valid = {'needs_research': False, 'reason': '简单问题', 'answer': '你好'}
        self.assertEqual(replay({**base, 'response': valid}), '未复现')

        old = {**base, 'context': {'prompt_id': 'runtime/route'}, 'response': {'action': 'direct'}}
        self.assertEqual(replay(old), '无法离线复现：未知提示词')
        trace = {'kind': 'workflow_trace', 'context': {'node': 'supervisor'}, 'response': {}}
        self.assertEqual(replay(trace), '只能诊断，无法离线复现')
        self.assertEqual(replay([]), '诊断格式无效')
        self.assertEqual(replay({'context': []}), '诊断格式无效')

    def test_write_failure_is_safe(self):
        with patch('src.infrastructure.diagnostics.data_directory', side_effect=OSError('sensitive')):
            self.assertEqual(record_failure(kind='network_error', payload={}, schema={}), '')

    def test_researcher_replay_uses_the_saved_stage(self):
        base = {'kind': 'model_validation', 'context': {'prompt_id': 'literature/researcher'}}
        finish = {'action': 'finish'}
        self.assertEqual(replay({**base, 'payload': {'phase': 'act'}, 'response': finish}), '未复现')
        self.assertTrue(replay({**base, 'payload': {'phase': 'draft'}, 'response': finish}).startswith('复现：'))
        draft = {'draft': {'sections': [], 'limitations': []}, 'findings': []}
        self.assertEqual(replay({**base, 'payload': {'phase': 'draft'}, 'response': draft}), '未复现')


if __name__ == '__main__':
    unittest.main()
