"""Shared local HTTP fixtures and lifecycle helpers for API/worker acceptance."""

import json
import re
import socket
import subprocess
import sys
import threading
import time
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
            results = [{
                'title': 'Simulated official accuracy results',
                'url': f'https://example.org/{slug}',
                'content': 'Accuracy was 92% in the simulated search result.',
            }]
            if 'dense-search-fixture' in folded:
                results.extend({
                    'title': f'Accuracy source {index}',
                    'url': f'https://example.org/{slug}/{index}',
                    'content': 'Accuracy was 92%. ' + 'Context from the simulated publisher. ' * 45,
                } for index in range(1, 20))
            self._send_json({'results': results})
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
        if (request.get('model') == 'fixture-thinking-budget'
                and request.get('reasoning_effort') == 'low'
                and request.get('max_completion_tokens') == 8192):
            error = {'error': {'code': 'invalid_parameter_error', 'param': None,
                               'message': 'max_completion_tokens [8192] must be greater than thinking_budget [10000]',
                               'type': 'invalid_request_error'}}
            self.send_response(400)
            self.send_header('Content-Type', 'text/event-stream;charset=utf-8')
            self.end_headers()
            self.wfile.write(f'data: {json.dumps(error)}\n\n'.encode('utf-8'))
            return
        result = self._model_result(role, payload, system)
        if (role == 'researcher' and payload.get('phase') == 'act') or role == 'route':
            assert request.get('tools') and request.get('tool_choice') == 'auto'
            assert 'response_format' not in request
            arguments = result.get('arguments', {})
            if result.get('reason'):
                arguments = {**arguments, 'reason': result['reason']}
            function_call = result.get('tool_name')
            tool_calls = [{
                'id': 'call_fixture', 'type': 'function',
                'function': {'name': function_call, 'arguments': json.dumps(arguments, ensure_ascii=False)},
            }] if function_call else []
            finish_reason = 'tool_calls' if tool_calls else 'stop'
            if request.get('stream') and tool_calls and 'stream-fixture' in payload.get('question', ''):
                call = tool_calls[0]
                encoded_args = call['function']['arguments']
                split = len(encoded_args) // 2
                chunks = [
                    {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                        'index': 0, 'id': call['id'], 'type': 'function',
                        'function': {'name': function_call, 'arguments': encoded_args[:split]},
                    }]}, 'finish_reason': None}]},
                    {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                        'index': 0, 'function': {'arguments': encoded_args[split:]},
                    }]}, 'finish_reason': None}]},
                    {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}]},
                ]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.end_headers()
                for chunk in chunks:
                    self.wfile.write(f'data: {json.dumps(chunk, ensure_ascii=False)}\n\n'.encode('utf-8'))
                self.wfile.write(b'data: [DONE]\n\n')
            else:
                message = {'role': 'assistant', 'content': None if tool_calls else '本轮证据已收集。'}
                if tool_calls:
                    message['tool_calls'] = tool_calls
                self._send_json({'choices': [{'index': 0, 'message': message, 'finish_reason': finish_reason}]})
            return
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        prompt_tokens = max(1, len(system) // 4) + max(1, len(messages[1].get('content', '')) // 4) if len(messages) > 1 else max(1, len(system) // 4)
        completion_tokens = max(1, len(content) // 4)
        if request.get('stream') and role == 'reporter' and 'stream-fixture' in payload.get('question', ''):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.end_headers()
            for index in range(0, len(content), 24):
                delta = {'content': content[index:index + 24], 'reasoning_content': 'private-fixture-sentinel'}
                body = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
                self.wfile.write(f'data: {json.dumps(body, ensure_ascii=False)}\n\n'.encode('utf-8'))
                self.wfile.flush()
                time.sleep(0.08)
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return
        self._send_json({
            'choices': [{'message': {'content': content},
                         'finish_reason': 'length' if role == 'planner' and 'partial-planner-fixture' in payload.get('question', '') else 'stop'}],
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
        if not role and 'markdown' in payload:
            return {'domains': ['模型评估'], 'tags': ['准确率'], 'description': '测试准确率'}
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
                if 'background-planning-fixture' in folded:
                    return {'source': 'web', 'queries': ['accuracy scope', 'accuracy methods', 'accuracy comparison']}
                return {
                    'source': preferred if preferred in allowed else allowed[0],
                    'queries': ['accuracy'] if 'context-cutoff-fixture' in folded else [question],
                    'selected_candidate_ids': [],
                    'summary': '',
                    'gaps': [],
                }
            candidates = payload.get('candidates', [])
            chosen = candidates[0] if candidates else None
            if 'background-planning-fixture' in folded:
                ids = [item['candidate_id'] for item in candidates]
                return {
                    'source': 'web', 'selected_candidate_ids': ids,
                    'additional_queries': [] if payload.get('final_selection') else ['accuracy limitations'],
                    'research_scope': '模拟准确率研究范围',
                    'confirmed_background': [{'text': '模拟来源报告准确率', 'candidate_ids': ids[:1]}] if ids else [],
                    'dimensions': ['研究方法', '比较条件'],
                    'controversies': [{'text': '比较条件需要核查', 'candidate_ids': ids[-1:]}] if ids else [],
                    'summary': '模拟背景筛选', 'gaps': ['核查研究条件'],
                }
            return {
                'source': mode if mode in {'web', 'paper'} else preferred,
                'queries': [],
                'selected_candidate_ids': [chosen['candidate_id']] if chosen else [],
                'summary': '仅按本地模拟服务返回的真实候选筛选。' if chosen else '搜索服务没有返回候选。',
                'gaps': [] if chosen else ['未找到可用候选'],
            }

        if role == 'planner':
            if 'partial-planner-fixture' in folded:
                return '# 研究计划\n\n## 研究要求\n- 查证准确率及其来源\n\n## 研究步骤\n- 检索原始记录并核对实验'
            return '# 研究计划\n\n## 研究要求\n- 查证准确率及其来源\n\n## 研究步骤\n- 收集并核对与问题直接相关的证据'

        if role == 'route':
            allowed = payload.get('allowed_sources') or ['paper']
            preferred = payload.get('current_source') or allowed[0]
            source = preferred if preferred in allowed else allowed[0]
            count = payload.get('round_count', 0)
            two_rounds = 'two-rounds' in folded or 'context-cutoff-fixture' in folded
            arguments = {'source': source}
            if count:
                action = 'research' if two_rounds and count == 1 else 'report'
                arguments.update(action=action, completion_confirmed=action == 'report')
                if two_rounds and count == 1:
                    arguments['next_steps'] = ['核查另一项独立来源的准确率结果']
            return {'tool_name': 'select_research_route', 'arguments': arguments}

        if role == 'researcher':
            candidates = payload.get('current_candidates', [])
            evidence = payload.get('evidence', [])
            mode = payload.get('current_source', 'paper')
            matching = [item for item in evidence if (
                item.get('source_level') in {'web_content', 'web_snippet'} if mode == 'web'
                else item.get('source_level') not in {'web_content', 'web_snippet'}
            )]
            if payload.get('phase') == 'act' and payload.get('local_domains') and not matching:
                documents = [
                    doc for result in payload.get('tool_results', [])
                    if result.get('tool') == 'select_local_domains'
                    for doc in result.get('documents', [])
                ]
                if documents:
                    selected = next((doc for doc in documents if 'accuracy' in doc['title'].casefold()), documents[0])
                    return {
                        'action': 'tool', 'tool_name': 'read_local_markdown',
                        'arguments': {'query': question, 'document_id': selected['document_id']},
                    }
                return {
                    'action': 'tool', 'tool_name': 'select_local_domains',
                    'arguments': {'domains': payload['local_domains'][:1]},
                }
            if payload.get('phase') == 'act' and candidates and not matching:
                return {
                    'action': 'tool',
                    'tool_name': 'collect_evidence',
                    'reason': '核对搜索结果中的准确率。',
                    'arguments': {'query': question, 'candidate_ids': [candidates[0]['candidate_id']]},
                }
            if payload.get('phase') == 'act':
                if mode == 'web' and ('research-failure' in folded or 'search-failure' in folded) and payload.get('search_attempts', 0) == 0:
                    return {
                        'action': 'tool',
                        'tool_name': 'search_web',
                        'arguments': {'query': question, 'limit': 5},
                    }
                return {'action': 'finish'}
            if matching:
                claim = '网页搜索摘要记录准确率为 92%。' if mode == 'web' else '论文证据记录准确率为 92%。'
                return f"# 研究结果\n\n{claim} [[{matching[0]['id']}]]"
            return '# 研究结果\n\n本轮没有获得可核查资料，无法确认准确率。'

        if role == 'analyst':
            has_evidence = bool(payload.get('evidence'))
            control = {
                'sufficient': has_evidence,
                'covered_requirements': ['准确率'] if has_evidence else [],
                'gaps': [] if has_evidence else ['没有取得可核查证据'],
                'next_steps': [] if has_evidence or 'research-failure' in folded else ['更换查询或来源后重新检索'],
                'unresolved_contradictions': [],
                'evidence_reliability_confirmed': has_evidence,
                'important_conclusions_cross_validated': has_evidence,
            }
            return '已审查当前证据。\n\n```tufa-control\n' + json.dumps(control, ensure_ascii=False) + '\n```'

        if role == 'reporter':
            evidence = payload.get('evidence', [])
            if evidence:
                source = evidence[0].get('source_level')
                claim = '网页搜索摘要记录准确率为 92%。' if source in {'web_content', 'web_snippet'} else '论文证据记录准确率为 92%。'
                citation_id = 'fixture-unavailable' if 'reporter-invalid-citation' in folded else evidence[0]['id']
                return f'# 准确率核查\n\n## 核查结果\n\n{claim} [[{citation_id}]]'
            return '# 准确率核查\n\n当前没有可核查证据，无法确认准确率。'

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
