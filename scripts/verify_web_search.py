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
    empty_failure_searches = 0
    rate_limit_searches = 0

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        req = json.loads(body)
        prompt = req['messages'][0]['content']
        data = json.loads(req['messages'][1]['content'])
        result = None
        if '你是研究主控' in prompt and 'typed-rate-limit' in data.get('question', ''):
            result = {'action': 'web_research', 'task': 'typed-rate-limit 政策', 'directions': ['政策']} if not data['results'] else {'action': 'report'}
        elif '你是研究专家' in prompt and 'typed-rate-limit' in data.get('task', ''):
            result = ({'action': 'search', 'query': 'typed-429', 'plan': ['检索']} if not data['recent_result'] else {'action': 'read_web_page', 'candidate_ids': [data['candidates'][0]['id']], 'plan': ['读取']} if data['candidates'] and '检索到' in data['recent_result'] else {'action': 'read', 'candidate_ids': [data['candidates'][0]['id']], 'plan': ['取证']} if data['candidates'] and '网页正文' in data['recent_result'] else {'action': 'finish', 'plan': ['完成']})
        elif '你是无工具研究审查者' in prompt and 'typed-rate-limit' in data.get('question', ''):
            result = {'sufficient': True, 'gaps': [], 'summary': '已检索', 'coverage': ['政策'], 'next_steps': [], 'subtask_findings': [{'text': '政策资料显示准确率为 92%', 'citations': [data['evidence'][0]['id']]}]}
        elif '你是研究主控' in prompt and 'empty-then-fail' in data.get('question', ''):
            result = {'action': 'web_research', 'task': 'empty-then-fail', 'directions': ['研究']} if not data['results'] else {'action': 'report'}
        elif '你是研究专家' in prompt and 'empty-then-fail' in data.get('task', ''):
            SearchFixture.empty_failure_searches += 1
            if data.get('search_blocked'):
                result = {'action': 'finish', 'plan': ['完成']}
            query = 'failure' if SearchFixture.empty_failure_searches == 6 else 'empty'
            if result is None: result = {'action': 'search', 'query': query, 'plan': ['检索']}
        elif '你是无工具研究审查者' in prompt and 'empty-then-fail' in data.get('question', ''):
            result = {'sufficient': False, 'gaps': ['未获得网页证据'], 'summary': '检索未完成', 'coverage': [], 'next_steps': []}
        if result is None:
            self.rfile = io.BytesIO(body)
            return super().do_POST()
        ModelFixture.requests.append(req)
        raw = json.dumps({'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/search':
            query = parse_qs(url.query).get('q', [''])[0]
            if query == 'typed-429':
                SearchFixture.rate_limit_searches += 1
                self.send_error(429)
                return
            if query == 'failure' or self.fail_search:
                self.send_error(503)
                return
            data = {'results': [] if query == 'empty' else [
                {'title': 'Simulated research page', 'url': 'https://example.org/research', 'content': 'Attention fixture paper uses attention to combine representations.' if 'Attention fixture' in query else 'accuracy was 92%'}]}
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


def install_page_fixture():
    # The real worker and HTML parser run with an in-process transport fixture.
    import src.integrations.web_page as page
    class FixtureResponse:
        status = 200
        def __init__(self): self.body = '<html><title>Fixture</title><article><h1>Method</h1><p>政策 Attention fixture paper uses attention to combine representations. accuracy was 92%</p></article></html>'.encode('utf-8')
        def getheader(self, name, default=None): return 'text/html; charset=utf-8' if name == 'Content-Type' else default
        def read(self, size):
            chunk, self.body = self.body[:size], self.body[size:]
            return chunk
    class FixtureConnection:
        def __init__(self, *args): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return FixtureResponse()
        def close(self): pass
    page._target = lambda url: (urlsplit(url), 'example.org', 443, '8.8.8.8')
    page._PinnedHTTPS = FixtureConnection
    import src.tools.search as search
    search.search_web_fallback = lambda query, limit=5: ([{'title': '政策 fixture', 'url': 'https://example.org/research', 'content': '政策 accuracy was 92%', 'source_level': 'web_snippet', 'provider': 'fixture_alternative', 'retrieved_at': '2026-11-09'}] if query == 'typed-429' else [])


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
            for command in ([sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1', '--port', str(port)], [sys.executable, '-c', "import scripts.fixture_api; import scripts.verify_web_search as f; f.install_page_fixture(); import runpy; runpy.run_module('apps.worker', run_name='__main__')"]):
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
                    assert evidence['source_level'] == 'web_content' and evidence['char_start'] == 0 and evidence['char_end'] > 0
                    assert evidence['provider'] == 'web_page' and evidence['retrieved_at']
                    assert call('GET', f'/projects/{project}/papers') == []
                    csv_text = client.get(f"/reports/{result['report_id']}/evidence.csv").text.lstrip('\ufeff')
                    assert next(csv.DictReader(io.StringIO(csv_text)))['provider'] == 'web_page'
                    assert len(call('GET', f"/conversations/{question['conversation_id']}/messages")) == 2
                    ModelFixture.requests.clear()
                    automatic = call('POST', f'/projects/{project}/questions', json={'question': '汇报一下最近七天国际大事'})
                    automatic_task = wait(automatic['run_id'])
                    assert automatic_task['status'] == 'succeeded' and automatic_task['source'] == 'auto', automatic_task
                    assert automatic_task['result']['evidence'][0]['provider'] == 'web_page'
                    routed = [json.loads(req['messages'][1]['content']) for req in ModelFixture.requests
                              if '你是研究主控' in req['messages'][0]['content']]
                    assert routed and routed[0]['source'] == 'auto'
                    assert {'direct', 'paper_research', 'web_research'} <= set(routed[0]['available_actions'])
                    assert call('GET', f'/projects/{project}/papers') == []
                    cross = call('POST', f'/projects/{project}/questions', json={'question': 'cross-source attention method', 'source': 'openalex'})
                    cross_task = wait(cross['run_id'])
                    assert cross_task['status'] == 'succeeded', cross_task
                    cross_result = cross_task['result']
                    assert all(('## ' + title) in cross_result['content'] for title in ('概述', '主题分析', '综合判断'))
                    assert {e['source_level'] for e in cross_result['evidence']} >= {'abstract', 'web_content'}
                    assert any(e.get('provider') == 'web_page' and e.get('original_url') for e in cross_result['evidence'])
                    cross_csv = client.get(f"/reports/{cross_result['report_id']}/evidence.csv").text.lstrip('\ufeff')
                    rows = list(csv.DictReader(io.StringIO(cross_csv)))
                    assert {row['source_level'] for row in rows} >= {'abstract', 'web_content'}
                    page_row = next(row for row in rows if row['source_level'] == 'web_content')
                    assert int(page_row['char_end']) > int(page_row['char_start']) >= 0
                    markdown = client.get(f"/reports/{cross_result['report_id']}/markdown").text
                    assert all(('## ' + title) in markdown for title in ('概述', '主题分析', '综合判断'))
                    assert all('[' + e['id'] + ']' in markdown for e in cross_result['evidence'])
                    assert '论文仅提供摘要，仍需核查原文。' in markdown
                    SearchFixture.empty_failure_searches = 0
                    ModelFixture.requests.clear()
                    mixed = call('POST', f'/projects/{project}/questions', json={'question': 'empty-then-fail', 'source': 'auto'})
                    mixed_task = wait(mixed['run_id'])
                    assert mixed_task['status'] == 'succeeded', mixed_task
                    assert SearchFixture.empty_failure_searches >= 1
                    assert not mixed_task['result']['evidence']
                    assert '证据不足' in mixed_task['result']['content']
                    assert mixed_task['result']['content'].startswith('# 阶段性研究结果')
                    assert not any('你是研究报告撰写者' in req['messages'][0]['content'] for req in ModelFixture.requests)
                    SearchFixture.rate_limit_searches = 0
                    ModelFixture.requests.clear()
                    limited = call('POST', f'/projects/{project}/questions', json={'question': 'typed-rate-limit', 'source': 'auto'})
                    limited_task = wait(limited['run_id'])
                    assert limited_task['status'] == 'succeeded', limited_task
                    assert SearchFixture.rate_limit_searches == 1
                    assert limited_task['result']['evidence']
                    assert limited_task['subtasks'] and limited_task['subtasks'][0]['sources']
                    assert limited_task['subtasks'][0]['status'] in ('findings', 'sources_only')
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
                        process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        provider.shutdown()
        provider.server_close()


if __name__ == '__main__':
    main()
