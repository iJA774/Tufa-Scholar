import logging
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from apps.api.main import app
from src.infrastructure.logging_setup import clear_application_logs, configure_logging
from src.persistence.storage import initialize_storage
from apps.worker.__main__ import run_once


def _log_after_clear(data_dir, ready, proceed):
    os.environ['TUFA_DATA_DIR'] = data_dir
    configure_logging('worker', max_bytes=100, backup_count=2)
    logging.getLogger('tufa.worker').info('worker before clear')
    ready.set()
    if not proceed.wait(10):
        raise RuntimeError('test coordination timed out')
    for index in range(10):
        logging.getLogger('tufa.worker').info('worker after clear event=%d with safe rotation data', index)
    from src.infrastructure.logging_setup import close_logging
    close_logging()


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        isolated_env = {'TUFA_DATA_DIR':self.tmp.name, 'OPENAI_API_KEY':'env-secret-abc'}
        for name in ('SystemRoot', 'WINDIR', 'PATH', 'TEMP', 'TMP'):
            if name in os.environ:
                isolated_env[name] = os.environ[name]
        self.env = patch.dict(os.environ, isolated_env, clear=True)
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
        from src.persistence.repository import update_stage
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

    def test_clear_logs_api_contract_scope_and_idempotency(self):
        logs = Path(self.tmp.name) / 'logs'
        logs.mkdir()
        for name in ('api.log', 'api.log.1', 'api.log.12', 'worker.log', 'worker.log.2'):
            (logs / name).write_text('old log data', encoding='utf-8')
        for name in ('other.log', 'api.log.old', '.tufa-logs.lock'):
            (logs / name).write_text('keep', encoding='utf-8')
        diagnostics = logs / 'diagnostics'
        diagnostics.mkdir()
        (diagnostics / 'private.json').write_text('keep diagnostics', encoding='utf-8')
        database = Path(self.tmp.name) / 'research.db'
        database_before = database.read_bytes()

        with TestClient(app) as client:
            response = client.delete('/settings/logs?path=outside')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'cleared_files': 5})
            self.assertEqual(client.delete('/settings/logs').json(), {'cleared_files': 0})

        self.assertFalse(any((logs / name).exists() for name in ('api.log', 'api.log.1', 'api.log.12', 'worker.log', 'worker.log.2')))
        self.assertEqual((logs / 'other.log').read_text(encoding='utf-8'), 'keep')
        self.assertEqual((logs / 'api.log.old').read_text(encoding='utf-8'), 'keep')
        self.assertEqual((diagnostics / 'private.json').read_text(encoding='utf-8'), 'keep diagnostics')
        self.assertEqual(database.read_bytes(), database_before)

    def test_clear_missing_logs_is_idempotent(self):
        self.assertFalse((Path(self.tmp.name) / 'logs').exists())
        self.assertEqual(clear_application_logs(), 0)
        self.assertFalse((Path(self.tmp.name) / 'logs').exists())

    def test_clear_failure_returns_safe_response(self):
        with TestClient(app) as client, patch('apps.api.main.clear_application_logs', side_effect=OSError('E:\\private\\secret.log')):
            response = client.delete('/settings/logs')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {'detail': '无法清除本地日志'})
        self.assertNotIn('secret.log', response.text)

    def test_active_writer_can_write_after_cross_process_clear(self):
        context = multiprocessing.get_context('spawn')
        ready, proceed = context.Event(), context.Event()
        configure_logging('api')
        logging.getLogger('tufa.api').info('api before clear')
        process = context.Process(target=_log_after_clear, args=(self.tmp.name, ready, proceed))
        process.start()
        try:
            self.assertTrue(ready.wait(15), 'worker did not start logging')
            removed = clear_application_logs()
            self.assertEqual(removed, 2)
            self.assertFalse((Path(self.tmp.name) / 'logs' / 'api.log').exists())
        finally:
            proceed.set()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        worker_log = (Path(self.tmp.name) / 'logs' / 'worker.log').read_text(encoding='utf-8')
        rotated_log = (Path(self.tmp.name) / 'logs' / 'worker.log.1').read_text(encoding='utf-8')
        self.assertIn('worker after clear', worker_log + rotated_log)
        self.assertNotIn('worker before clear', worker_log)

if __name__=='__main__': unittest.main()
