"""Shared local HTTP fixtures and lifecycle helpers for API/worker acceptance."""

import json
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        super().__init__(address, FixtureHandler)
        self.model_requests = []
        self.search_requests = []
        self.search_failure_remaining = 0
        self.search_failure_armed = False


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == '/search':
            query = parse_qs(parts.query).get('q', [''])[0]
            self.server.search_requests.append(query)
            folded = query.casefold()
            if 'research-failure' in folded and not self.server.search_failure_armed:
                self.server.search_failure_armed = True
                self.server.search_failure_remaining = 4
            if self.server.search_failure_remaining:
                self.server.search_failure_remaining -= 1
                self.send_error(503)
                return
            if 'search-failure' in folded or 'provider-failure' in folded:
                self.send_error(503)
                return
            if 'no-evidence' in folded or 'empty-results' in folded:
                self._send_json({'results': []})
                return
            slug = re.sub(r'[^a-z0-9]+', '-', folded).strip('-')[:60] or 'fixture'
            self._send_json({'results': [{
                'title': 'Simulated official accuracy results',
                'url': f'https://example.org/{slug}',
                'content': 'Accuracy was 92% in the simulated search result.',
            }]})
            return
        if parts.path == '/v1/models':
            self._send_json({'data': [{'id': 'fixture'}]})
            return
        self.send_error(404)

    def do_POST(self):
        if urlsplit(self.path).path != '/v1/chat/completions':
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self.server.model_requests.append(request)
        messages = request.get('messages', [])
        system = messages[0].get('content', '') if messages else ''
        payload = json.loads(messages[1].get('content', '{}')) if len(messages) > 1 else {}
        match = re.search(r'运行节点标识：([a-z_]+)', system)
        role = match.group(1) if match else ''
        result = self._model_result(role, payload, system)
        content = json.dumps(result, ensure_ascii=False)
        prompt_tokens = max(1, len(system) // 4) + max(1, len(messages[1].get('content', '')) // 4) if len(messages) > 1 else max(1, len(system) // 4)
        completion_tokens = max(1, len(content) // 4)
        self._send_json({
            'choices': [{'message': {'content': content}}],
            'usage': {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': prompt_tokens + completion_tokens,
                'prompt_tokens_details': {'cached_tokens': 0},
                'completion_tokens_details': {'reasoning_tokens': 0},
            },
        })

    @staticmethod
    def _model_result(role, payload, system=''):
        question = str(payload.get('question', ''))
        folded = question.casefold()
        if role == 'dispatcher':
            direct = folded.startswith('direct:')
            quick_mode = '当前是快速探索模式' in system
            return {
                # For quick mode, deliberately contradict the forced-direct policy.
                # The application must still return this answer without entering research.
                'needs_research': not direct,
                'reason': '本地模拟入口分流；quick 模式下故意请求研究以覆盖强制模式边界',
                'answer': ('这是不需要检索的直接回答。' if direct else
                           '这是 quick 模式应返回的直接回答。' if quick_mode else ''),
            }

        if role == 'background_investigator':
            allowed = payload.get('available_sources') or ['paper', 'web']
            preferred = 'web' if payload.get('source') == 'web' and 'web' in allowed else 'paper'
            mode = payload.get('search_mode') or preferred
            if payload.get('phase') == 'query':
                return {
                    'source': preferred if preferred in allowed else allowed[0],
                    'queries': [question],
                    'selected_candidate_ids': [],
                    'summary': '',
                    'gaps': [],
                }
            candidates = payload.get('candidates', [])
            chosen = candidates[0] if candidates else None
            return {
                'source': mode if mode in {'web', 'paper'} else preferred,
                'queries': [],
                'selected_candidate_ids': [chosen['candidate_id']] if chosen else [],
                'summary': '仅按本地模拟服务返回的真实候选筛选。' if chosen else '搜索服务没有返回候选。',
                'gaps': [] if chosen else ['未找到可用候选'],
            }

        if role == 'planner':
            allowed = payload.get('allowed_sources') or ['paper']
            preferred = payload.get('source_preference') or ('web' if payload.get('source') == 'web' else 'paper')
            source = preferred if preferred in allowed else allowed[0]
            count = payload.get('round_count', 0)
            two_rounds = 'two-rounds' in question.casefold()
            return {
                'requirements': ['查证准确率及其来源'],
                'plan': ['收集并核对与问题直接相关的证据'],
                'source': source,
                'action': 'research' if count == 0 or (two_rounds and count == 1) else 'report',
                'rationale': '使用隔离 HTTP 验收计划。',
            }

        if role == 'researcher':
            candidates = payload.get('current_candidates', [])
            evidence = payload.get('evidence', [])
            mode = payload.get('current_source', 'paper')
            matching = [item for item in evidence if (
                item.get('source_level') in {'web_content', 'web_snippet'} if mode == 'web'
                else item.get('source_level') not in {'web_content', 'web_snippet'}
            )]
            if candidates and not matching:
                return {
                    'action': 'tool',
                    'tool_name': 'collect_evidence',
                    'arguments': {'query': question, 'candidate_ids': [candidates[0]['candidate_id']]},
                    'draft': {'sections': [], 'limitations': []},
                    'findings': [],
                }
            if mode == 'web' and ('research-failure' in folded or 'search-failure' in folded) and payload.get('search_attempts', 0) == 0:
                return {
                    'action': 'tool',
                    'tool_name': 'search_web',
                    'arguments': {'query': question, 'limit': 5},
                    'draft': {'sections': [], 'limitations': []},
                    'findings': [],
                }
            if matching:
                claim = '网页搜索摘要记录准确率为 92%。' if mode == 'web' else '论文证据记录准确率为 92%。'
                return {
                    'action': 'finish', 'tool_name': '', 'arguments': {},
                    'draft': {'sections': [{'heading': '研究结果', 'paragraphs': [{'text': claim, 'citations': [matching[0]['id']]}]}], 'limitations': []},
                    'findings': [{'text': claim, 'citations': [matching[0]['id']]}],
                }
            return {
                'action': 'finish', 'tool_name': '', 'arguments': {},
                'draft': {'sections': [], 'limitations': [{'text': '本轮没有获得可核查资料，无法确认准确率。', 'citations': []}]},
                'findings': [],
            }

        if role == 'analyst':
            has_evidence = bool(payload.get('evidence'))
            return {
                'sufficient': has_evidence,
                'covered_requirements': ['准确率'] if has_evidence else [],
                'gaps': [] if has_evidence else ['没有取得可核查证据'],
                'next_steps': [] if has_evidence else ['更换查询或来源后重新检索'],
                'review': '模拟审查了本轮初稿及证据。',
            }

        if role == 'reporter':
            evidence = payload.get('evidence', [])
            if evidence:
                source = evidence[0].get('source_level')
                claim = '网页搜索摘要记录准确率为 92%。' if source in {'web_content', 'web_snippet'} else '论文证据记录准确率为 92%。'
                return {
                    'sections': [{'heading': '核查结果', 'paragraphs': [{'text': claim, 'citations': [evidence[0]['id']]}]}],
                    'limitations': [],
                }
            return {
                'sections': [],
                'limitations': [{'text': '当前没有可核查证据，无法确认准确率。', 'citations': []}],
            }

        raise AssertionError(f'Unexpected model node role: {role}')


def start_fixture_server():
    server = FixtureHTTPServer(('127.0.0.1', 0))
    thread = threading.Thread(target=server.serve_forever, name='tufa-fixture-http', daemon=True)
    thread.start()
    return server, thread


def stop_processes(processes):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    return all(process.poll() is not None for process in processes)


def assert_isolated_services(client, processes, data_directory):
    """Refuse to write unless both children live and API reports the temp DB."""
    if any(process.poll() is not None for process in processes):
        raise AssertionError('Self-started API or worker exited before acceptance writes')
    response = client.get('/health')
    response.raise_for_status()
    database_path = Path(response.json()['database']['path']).resolve()
    expected_path = (Path(data_directory) / 'research.db').resolve()
    if database_path != expected_path:
        raise AssertionError('Health endpoint is not connected to the isolated acceptance database')
    if any(process.poll() is not None for process in processes):
        raise AssertionError('Self-started API or worker exited during isolation check')


def api_commands(port):
    return [
        [sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1', '--port', str(port)],
        [sys.executable, '-m', 'apps.worker'],
    ]
