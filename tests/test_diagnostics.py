import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from src.infrastructure.diagnostics import diagnostic_scope, record_failure
from scripts.replay_diagnostic import replay


class DiagnosticsTest(unittest.TestCase):
    def test_record_redact_scope_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp, patch('src.infrastructure.diagnostics.data_directory', return_value=Path(tmp)), patch('src.infrastructure.settings._local', return_value={'openai_api_key': 'private-value'}):
            with diagnostic_scope(run_id=42):
                ident = record_failure(kind='model_validation', payload={'api_key': 'hidden', 'nested': ['private-value'], 'private-value': 'x'}, schema={}, response='{"action":"bad", "authorization":"hidden"}', context={'prompt_id': 'runtime/route'})
            text = (Path(tmp) / 'diagnostics' / (ident + '.json')).read_text(encoding='utf-8')
            self.assertNotIn('private-value', text)
            self.assertNotIn('hidden', text)
            artifact = json.loads(text)
            self.assertEqual(artifact['context']['run_id'], 42)
            self.assertTrue(artifact['redacted'])
            self.assertIn('python', artifact['environment'])
            result = subprocess.run([sys.executable, '-B', '-c', "import socket, sqlite3, runpy; socket.socket.connect = sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('forbidden')); runpy.run_module('scripts.replay_diagnostic', run_name='__main__')", str(Path(tmp) / 'diagnostics' / (ident + '.json'))], capture_output=True, text=True, encoding='utf-8', env={**__import__('os').environ, 'TUFA_DATA_DIR': str(Path(tmp) / 'must-not-exist'), 'PYTHONIOENCODING': 'utf-8'})
            self.assertEqual(result.returncode, 0)
            self.assertIn('复现', result.stdout)
            self.assertFalse((Path(tmp) / 'must-not-exist').exists())
            self.assertTrue(replay(artifact).startswith('复现'))
            artifact['response'] = '{"action":"review"}'
            self.assertEqual(replay(artifact), '未复现')
            second = record_failure(kind='network_error', payload={}, schema={})
            self.assertNotIn('run_id', json.loads((Path(tmp) / 'diagnostics' / (second + '.json')).read_text(encoding='utf-8'))['context'])

    def test_semantic_replay(self):
        self.assertEqual(replay([]), '诊断格式无效')
        self.assertEqual(replay({'context': []}), '诊断格式无效')
        route = {'kind': 'semantic_route', 'response': {'action': 'direct'}, 'context': {'allowed_actions': ['review']}}
        self.assertEqual(replay(route), '复现')
        route['response']['action'] = 'review'
        self.assertEqual(replay(route), '未复现')
        candidates = {'kind': 'semantic_candidates', 'response': {'candidate_ids': ['C1', 'C1']}, 'context': {'candidate_ids': ['C1']}}
        self.assertEqual(replay(candidates), '复现')
        candidates['response']['candidate_ids'] = ['C1']
        self.assertEqual(replay(candidates), '未复现')

    def test_write_failure_is_safe(self):
        with patch('src.infrastructure.diagnostics.data_directory', side_effect=OSError('sensitive')):
            self.assertEqual(record_failure(kind='network_error', payload={}, schema={}), '')
