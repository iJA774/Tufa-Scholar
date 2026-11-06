import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from apps.api.main import app
from src.logging_setup import configure_logging
from src.storage import initialize_storage
from apps.worker.__main__ import run_once


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR':self.tmp.name,'OPENAI_API_KEY':'env-secret-abc'}, clear=True)
        self.env.start(); initialize_storage()
    def tearDown(self):
        for name in ('src.integrations','src.literature.retrieval'):
            logging.getLogger(name).handlers.clear()
        logger = logging.getLogger('tufa')
        for h in logger.handlers[:]: logger.removeHandler(h); h.close()
        self.env.stop(); self.tmp.cleanup()
    def test_api_records_route_not_query_and_redacts_keys(self):
        with TestClient(app) as c:
            c.put('/settings',json={'openalex_api_key':'local-secret-abc'})
            c.get('/health',params={'q':'env-secret-abc'})
            c.get('/not-a-route/local-secret-abc')
            c.get('/projects/4321/papers', params={'q':'local-secret-abc'})
            logging.getLogger('tufa.api').error('url=https://example.test/path?key=local-secret-abc env-secret-abc')
        text = (Path(self.tmp.name)/'logs/api.log').read_text(encoding='utf-8')
        self.assertIn("path=/health ids={} status=200",text)
        self.assertIn('path=<unmatched> ids={} status=404',text)
        self.assertIn("path=/projects/{project_id}/papers ids={'project_id': 4321} status=404",text)
        self.assertNotIn('/projects/4321/papers',text)
        self.assertNotIn('env-secret-abc',text); self.assertNotIn('local-secret-abc',text); self.assertNotIn('https://example.test',text)
    def test_worker_failure_logs_safe_frames_and_phase(self):
        configure_logging('worker')
        task={'id':7,'project_id':2,'source':'local','kind':'search','query':'secret question','collection_id':None}
        with patch('apps.worker.__main__.repository.claim_task',return_value=task), patch('apps.worker.__main__.repository.fail_task') as fail, patch('apps.worker.__main__.ToolRegistry.invoke',side_effect=RuntimeError('https://example.test?key=env-secret-abc')):
            run_once()
        text=(Path(self.tmp.name)/'logs/worker.log').read_text(encoding='utf-8')
        self.assertIn('task_start run_id=7',text); self.assertIn('task_failed run_id=7',text); self.assertIn('RuntimeError',text)
        self.assertNotIn('env-secret-abc',text); self.assertNotIn('secret question',text); self.assertNotIn('https://',text)
        self.assertNotIn('env-secret-abc',fail.call_args.args[1])
        from src.repository import update_stage
        update_stage(7,'searching')
        self.assertIn('task_stage run_id=7 stage=searching',(Path(self.tmp.name)/'logs/worker.log').read_text(encoding='utf-8'))
    def test_known_http_status_is_safe_but_appended_secret_is_not(self):
        configure_logging('worker')
        task={'id':8,'project_id':2,'source':'local','kind':'search','query':'q','collection_id':None}
        with patch('apps.worker.__main__.repository.claim_task',return_value=task), patch('apps.worker.__main__.repository.fail_task') as fail:
            with patch('apps.worker.__main__.ToolRegistry.invoke',side_effect=RuntimeError('模型服务请求失败 (HTTP 401)')):
                run_once()
            self.assertEqual(fail.call_args.args[1],'模型服务请求失败 (HTTP 401)')
            with patch('apps.worker.__main__.ToolRegistry.invoke',side_effect=RuntimeError('模型服务请求失败 (HTTP 401) env-secret-abc')):
                run_once()
            self.assertNotIn('env-secret-abc',fail.call_args.args[1])

    def test_rotation(self):
        configure_logging('worker',max_bytes=100,backup_count=2)
        for _ in range(20): logging.getLogger('tufa.worker').info('event safe data for rotation')
        self.assertTrue((Path(self.tmp.name)/'logs/worker.log.1').exists())

if __name__=='__main__': unittest.main()
