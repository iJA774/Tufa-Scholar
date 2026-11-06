"""Real local API/worker with simulated SearX and model HTTP; no cloud calls."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
from scripts.verify_chat import ModelFixture, free_port

ROOT = Path(__file__).resolve().parents[1]


class SearchFixture(ModelFixture):
    fail_search = False
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/search':
            query = parse_qs(url.query).get('q', [''])[0]
            if query == 'failure' or self.fail_search:
                self.send_error(503)
                return
            data = {'results': [] if query == 'empty' else [
                {'title': 'Simulated research page', 'url': 'https://example.org/research', 'content': 'accuracy was 92%'}]}
        elif url.path == '/v1/models':
            data = {'data': [{'id': 'fixture'}]}
        else:
            self.send_error(404)
            return
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    provider = ThreadingHTTPServer(('127.0.0.1', 0), SearchFixture)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    processes = []
    try:
        with tempfile.TemporaryDirectory(prefix='tufa-web-') as directory, open(os.devnull, 'wb') as sink:
            env = os.environ.copy()
            for name in ('SEARCH_API', 'SEARX_HOST', 'WIKIPEDIA_LANG', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENALEX_API_KEY', 'TAVILY_API_KEY', 'INFOQUEST_API_KEY', 'BRAVE_SEARCH_API_KEY', 'SERPER_API_KEY'):
                env.pop(name, None)
            env['TUFA_DATA_DIR'] = directory
            port = int(os.environ.get('TUFA_VERIFY_API_PORT', '0')) or free_port()
            base = f'http://127.0.0.1:{port}'
            for command in ([sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1', '--port', str(port)], [sys.executable, '-m', 'apps.worker']):
                processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=sink, stderr=sink))
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
                    for _ in range(100):
                        try:
                            if client.get('/health').status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        time.sleep(.2)
                    else:
                        raise AssertionError('API did not start')

                    def call(method, path, **kwargs):
                        response = client.request(method, path, **kwargs)
                        response.raise_for_status()
                        return response.json()

                    def wait(run_id):
                        for _ in range(150):
                            task = call('GET', f'/runs/{run_id}')
                            if task['status'] in ('succeeded', 'failed', 'interrupted'):
                                return task
                            time.sleep(.2)
                        raise AssertionError('worker timed out')

                    settings = call('PUT', '/settings', json={'web_search_engine': 'searx', 'searx_base_url': f'http://127.0.0.1:{provider.server_port}', 'openai_base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'openai_model': 'fixture'})
                    assert len(settings['web_search']['providers']) == 8
                    project = call('POST', '/projects', json={'name': '网页搜索模拟验收'})['id']
                    route = f'/projects/{project}/web-search'
                    found = call('GET', route, params={'q': 'accuracy'})['results']
                    assert found[0]['provider'] == 'searx' and found[0]['source_level'] == 'web_snippet'
                    assert call('GET', route, params={'q': 'empty'}) == {'results': []}
                    assert client.get(route, params={'q': 'failure'}).status_code == 503
                    question = call('POST', f'/projects/{project}/questions', json={'question': 'accuracy?', 'source': 'web'})
                    task = wait(question['run_id'])
                    assert task['status'] == 'succeeded', task
                    result = task['result']
                    assert '92%' in result['content'] and '30%' not in result['content']
                    evidence = result['evidence'][0]
                    assert evidence['original_url'] == 'https://example.org/research'
                    assert evidence['provider'] == 'searx' and evidence['retrieved_at']
                    assert call('GET', f'/projects/{project}/papers') == []
                    csv_text = client.get(f"/reports/{result['report_id']}/evidence.csv").text.lstrip('\ufeff')
                    assert next(csv.DictReader(io.StringIO(csv_text)))['provider'] == 'searx'
                    assert len(call('GET', f"/conversations/{question['conversation_id']}/messages")) == 2
                    SearchFixture.fail_search = True
                    failed = call('POST', f'/projects/{project}/questions', json={'question': 'accuracy failure?', 'source': 'web'})
                    failed_task = wait(failed['run_id'])
                    assert failed_task['status'] == 'failed' and '搜索设置' in failed_task['error'], failed_task
                    SearchFixture.fail_search = False
                    retried = call('POST', f"/runs/{failed['run_id']}/retry")
                    assert wait(retried['id'])['status'] == 'succeeded'
                    print('Simulated SearX/model: real API, worker, citations, CSV, empty/error and paper isolation passed.', flush=True)
                    if '--serve' in sys.argv:
                        print(f'API: {base}; isolated data: {directory}', flush=True)
                        try:
                            while True:
                                time.sleep(1)
                        except KeyboardInterrupt:
                            pass
            finally:
                for process in processes:
                    if process.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            process.terminate()
                for process in processes:
                    process.wait(timeout=10)
    finally:
        provider.shutdown()
        provider.server_close()


if __name__ == '__main__':
    main()
