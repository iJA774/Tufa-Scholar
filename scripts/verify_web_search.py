"""Verify web search through the real API and worker with local HTTP fixtures."""

import os
import re
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from scripts.verification_support import ROOT, api_commands, assert_isolated_services, free_port, start_fixture_server, stop_processes


def main():
    temporary = tempfile.TemporaryDirectory(prefix='tufa-web-verify-')
    fixture = None
    fixture_thread = None
    processes = []
    sink = None
    stopped = True
    try:
        fixture, fixture_thread = start_fixture_server()
        sink = open(os.devnull, 'wb')
        port = int(os.environ.get('TUFA_VERIFY_API_PORT', '0')) or free_port()
        base = f'http://127.0.0.1:{port}'
        env = os.environ.copy()
        for name in (
            'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENALEX_API_KEY',
            'SEARCH_API', 'SEARX_HOST', 'WIKIPEDIA_LANG', 'TAVILY_API_KEY',
            'INFOQUEST_API_KEY', 'BRAVE_SEARCH_API_KEY', 'SERPER_API_KEY',
        ):
            env.pop(name, None)
        env['TUFA_DATA_DIR'] = temporary.name
        for command in api_commands(port):
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=sink, stderr=sink))

        with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
            for _ in range(100):
                try:
                    health = client.get('/health')
                    if health.status_code == 200:
                        assert_isolated_services(client, processes, temporary.name)
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.2)
            else:
                raise AssertionError('Local API failed to start')

            def call(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()

            def wait(run_id):
                for _ in range(300):
                    task = call('GET', f'/runs/{run_id}')
                    if task['status'] in {'succeeded', 'failed', 'interrupted'}:
                        return task
                    time.sleep(0.2)
                raise AssertionError(f'Worker timed out for run {run_id}')

            def stages(run_id):
                log_path = Path(temporary.name) / 'logs' / 'worker.log'
                if not log_path.exists():
                    return []
                pattern = re.compile(rf'task_stage run_id={run_id} stage=([a-z_]+)')
                return [match.group(1) for match in pattern.finditer(log_path.read_text(encoding='utf-8', errors='replace'))]

            call('PUT', '/settings', json={
                'web_search_engine': 'searx',
                'searx_base_url': f'http://127.0.0.1:{fixture.server_port}',
                'openai_base_url': f'http://127.0.0.1:{fixture.server_port}/v1',
                'openai_model': 'fixture-web',
            })
            project = call('POST', '/projects', json={'name': 'Isolated web acceptance'})['id']

            route = f'/projects/{project}/web-search'
            candidates = call('GET', route, params={'q': 'accuracy'})['results']
            assert candidates and candidates[0]['provider'] == 'searx'
            assert candidates[0]['source_level'] == 'web_snippet'
            assert candidates[0]['url'].startswith('https://example.org/')
            failed_search = client.get(route, params={'q': 'provider-failure'})
            assert failed_search.status_code == 503
            assert '网络搜索失败' in failed_search.text

            # The fixture dispatcher deliberately asks for research in quick mode.
            # The worker must honor the stored mode and finish without background/search tools.
            searches_before_quick = len(fixture.search_requests)
            requests_before_quick = len(fixture.model_requests)
            quick = call('POST', f'/projects/{project}/questions', json={
                'question': 'quick-mode contradiction accuracy', 'source': 'web', 'research_mode': 'quick',
            })
            quick_task = wait(quick['run_id'])
            assert quick_task['status'] == 'succeeded', quick_task
            assert quick_task['research_mode'] == 'quick', quick_task
            assert quick_task['result']['evidence'] == []
            assert 'quick 模式应返回的直接回答' in quick_task['result']['content']
            quick_requests = fixture.model_requests[requests_before_quick:]
            quick_roles = [
                line.split('运行节点标识：', 1)[1].splitlines()[0].strip()
                for item in quick_requests
                for line in item['messages'][0]['content'].splitlines()
                if '运行节点标识：' in line
            ]
            assert quick_roles == ['dispatcher'], quick_roles
            assert stages(quick['run_id']) == ['dispatcher'], stages(quick['run_id'])
            assert len(fixture.search_requests) == searches_before_quick, fixture.search_requests[searches_before_quick:]

            # Deep mode must research even a greeting; production bypasses the dispatcher.
            searches_before_deep = len(fixture.search_requests)
            requests_before_deep = len(fixture.model_requests)
            deep = call('POST', f'/projects/{project}/questions', json={
                'question': 'hello', 'source': 'web', 'research_mode': 'deep',
            })
            deep_task = wait(deep['run_id'])
            assert deep_task['status'] == 'succeeded', deep_task
            assert deep_task['research_mode'] == 'deep', deep_task
            deep_requests = fixture.model_requests[requests_before_deep:]
            deep_roles = [
                line.split('运行节点标识：', 1)[1].splitlines()[0].strip()
                for item in deep_requests
                for line in item['messages'][0]['content'].splitlines()
                if '运行节点标识：' in line
            ]
            assert deep_roles and deep_roles[0] == 'background_investigator', deep_roles
            assert 'dispatcher' not in deep_roles, deep_roles
            deep_stages = stages(deep['run_id'])
            assert 'background_investigator' in deep_stages and deep_stages[-1:] == ['reporter'], deep_stages
            assert len(fixture.search_requests) > searches_before_deep, fixture.search_requests[searches_before_deep:]

            question = call('POST', f'/projects/{project}/questions', json={
                'question': 'accuracy', 'source': 'web', 'model_name': 'fixture-web-selected',
            })
            task = wait(question['run_id'])
            assert task['status'] == 'succeeded', task
            result = task['result']
            assert task['stage'] == 'completed'
            assert stages(question['run_id'])[-1:] == ['reporter'], stages(question['run_id'])
            assert '92%' in result['content']
            evidence = result['evidence']
            assert len(evidence) == 1
            assert evidence[0]['source_level'] == 'web_snippet'
            assert evidence[0]['provider'] == 'searx'
            assert evidence[0]['original_url'] == candidates[0]['url']
            assert evidence[0]['retrieved_at']
            assert call('GET', f'/projects/{project}/papers') == []
            report_csv = client.get(f"/reports/{result['report_id']}/evidence.csv").text
            assert 'web_snippet' in report_csv and 'searx' in report_csv
            assert len(call('GET', f"/conversations/{question['conversation_id']}/messages")) == 2

            dense_question = 'dense-search-fixture accuracy'
            dense = call('POST', f'/projects/{project}/questions', json={
                'question': dense_question, 'source': 'web', 'research_mode': 'deep',
            })
            dense_task = wait(dense['run_id'])
            assert dense_task['status'] == 'succeeded', dense_task
            assert dense_task['result']['evidence'] and '92%' in dense_task['result']['content']
            assert '尚需核对更多独立来源' in dense_task['result']['content']
            dense_calls = [
                (request, json.loads(request['messages'][1]['content']))
                for request in fixture.model_requests
                if json.loads(request['messages'][1]['content']).get('question') == dense_question
            ]
            background = [payload for _, payload in dense_calls if payload.get('phase') == 'select']
            assert len(background) == 1 and 1 <= len(background[0]['candidates']) <= 3, background
            decisions = [(request, payload) for request, payload in dense_calls if payload.get('phase') == 'act']
            assert decisions and all(1 <= len(payload['current_candidates']) <= 3 for _, payload in decisions), decisions
            assert all(request.get('reasoning_effort') == 'low' and request.get('max_completion_tokens') == 4096
                       for request, _ in decisions)
            writing = [request for request, payload in dense_calls if payload.get('phase') == 'draft'
                       or '运行节点标识：reporter' in request['messages'][0]['content']]
            assert len(writing) == 2 and all('max_completion_tokens' not in request for request in writing), writing

            before_conflict = len(fixture.model_requests)
            conflict = call('POST', f'/projects/{project}/questions', json={
                'question': 'thinking-budget-fixture accuracy', 'source': 'web',
                'research_mode': 'deep', 'model_name': 'fixture-thinking-budget',
            })
            conflict_task = wait(conflict['run_id'])
            assert conflict_task['status'] == 'succeeded' and conflict_task['result']['evidence'], conflict_task
            conflict_decisions = [request for request in fixture.model_requests[before_conflict:]
                                  if request.get('reasoning_effort') == 'low']
            assert len(conflict_decisions) >= 4
            assert conflict_decisions[0].get('max_completion_tokens') == 4096
            assert all('max_completion_tokens' not in request for request in conflict_decisions[1:])
            assert conflict_decisions[0]['messages'] == conflict_decisions[1]['messages']

            cutoff_question = 'context-cutoff-fixture ' + '核对这项研究的准确率。' * 1_000
            searches_before = len(fixture.search_requests)
            requests_before = len(fixture.model_requests)
            cutoff = call('POST', f'/projects/{project}/questions', json={
                'question': cutoff_question, 'source': 'web', 'research_mode': 'deep',
            })
            cutoff_run = cutoff['run_id']
            cutoff_task = wait(cutoff_run)
            assert cutoff_task['status'] == 'succeeded', cutoff_task
            assert '上下文预算' in cutoff_task['result']['content']
            assert fixture.search_requests[searches_before:] == ['accuracy']
            cutoff_phases = [json.loads(request['messages'][1]['content']).get('phase')
                             for request in fixture.model_requests[requests_before:]]
            assert cutoff_phases == ['query', 'select', None, 'draft', None, None], cutoff_phases
            assert stages(cutoff_run).count('researcher') == 1
            assert stages(cutoff_run)[-1:] == ['reporter']
            worker_log = (Path(temporary.name) / 'logs' / 'worker.log').read_text(encoding='utf-8')
            assert f'research_round_stopped run_id={cutoff_run} reason=decision_context_budget' in worker_log

            streamed_question = call('POST', f'/projects/{project}/questions', json={
                'question': 'stream-fixture accuracy', 'source': 'web', 'research_mode': 'deep',
            })
            stream_run = streamed_question['run_id']
            events_path = f'/runs/{stream_run}/events'
            events = []
            live_delta = False
            with client.stream('GET', events_path) as event_response:
                event_response.raise_for_status()
                assert event_response.headers['content-type'].startswith('text/event-stream')
                for line in event_response.iter_lines():
                    if not line.startswith('data: '):
                        continue
                    assert 'private-fixture-sentinel' not in line
                    event = json.loads(line[6:])
                    events.append(event)
                    if not live_delta and event['kind'] == 'model.delta' and event['data']['node'] == 'reporter':
                        live_delta = call('GET', f'/runs/{stream_run}')['status'] == 'running'
            assert live_delta, 'Reporter streamed content must arrive before the report commits'
            assert wait(stream_run)['status'] == 'succeeded'
            assert any(event['kind'] == 'node.completed' and event['data'].get('node') == 'planner'
                       and event['data'].get('source') == 'web' for event in events)
            assert any(event['kind'] == 'tool.started' and event['data'].get('reason') == '核对搜索结果中的准确率。'
                       for event in events)
            assert all(event['data'].get('node') != 'dispatcher' for event in events if event['kind'].startswith('node.'))
            assert 'data: ' not in client.get(events_path, params={'after': events[-1]['id']}).text

            rejected_report = call('POST', f'/projects/{project}/questions', json={
                'question': 'reporter-invalid-citation accuracy', 'source': 'web',
            })
            recovered = wait(rejected_report['run_id'])
            assert recovered['status'] == 'succeeded', recovered
            recovered_report = recovered['result']
            assert '证据不足' in recovered_report['content']
            assert '92%' in recovered_report['content']
            assert 'fixture-unavailable' not in recovered_report['content']
            stage_citations = {identifier for step in recovered['subtasks']
                               for finding in step['findings'] for identifier in finding['citations']}
            recovered_ids = {item['id'] for item in recovered_report['evidence']}
            assert recovered_ids and recovered_ids == stage_citations
            recovered_csv = client.get(f"/reports/{recovered_report['report_id']}/evidence.csv")
            assert recovered_csv.status_code == 200 and all(identifier in recovered_csv.text for identifier in recovered_ids)
            recovered_markdown = client.get(f"/reports/{recovered_report['report_id']}/markdown")
            assert recovered_markdown.status_code == 200 and recovered_markdown.text == recovered_report['content']

            two_round = call('POST', f'/projects/{project}/questions', json={
                'question': 'two-rounds accuracy', 'source': 'web',
            })
            two_round_task = wait(two_round['run_id'])
            assert two_round_task['status'] == 'succeeded', two_round_task
            assert stages(two_round['run_id']).count('researcher') == 2
            reporter_payloads = [
                json.loads(request['messages'][1]['content'])
                for request in fixture.model_requests
                if '运行节点标识：reporter' in request['messages'][0]['content']
                and json.loads(request['messages'][1]['content']).get('question') == 'two-rounds accuracy'
            ]
            assert len(reporter_payloads) == 1 and len(reporter_payloads[0]['rounds']) == 2
            assert stages(two_round['run_id'])[-1:] == ['reporter']

            # A provider failure must still yield a completed, evidence-free report.
            before_failure_searches = len(fixture.search_requests)
            before_failure_requests = len(fixture.model_requests)
            failed_question = call('POST', f'/projects/{project}/questions', json={
                'question': 'research-failure accuracy', 'source': 'web',
            })
            failed_task = wait(failed_question['run_id'])
            assert failed_task['status'] == 'succeeded', failed_task
            assert failed_task['stage'] == 'completed'
            assert stages(failed_question['run_id'])[-1:] == ['reporter'], stages(failed_question['run_id'])
            assert failed_task['result']['evidence'] == []
            assert '没有可核查证据' in failed_task['result']['content'] or '证据不足' in failed_task['result']['content']
            failure_searches = fixture.search_requests[before_failure_searches:]
            assert len(failure_searches) == 3, failure_searches
            assert len(set(failure_searches)) >= 2, failure_searches

            failure_requests = fixture.model_requests[before_failure_requests:]
            failure_payloads = []
            for request in failure_requests:
                system = request['messages'][0]['content']
                match = re.search(r'运行节点标识：([a-z_]+)', system)
                payload = json.loads(request['messages'][1]['content'])
                if payload.get('question') == 'research-failure accuracy':
                    failure_payloads.append((match.group(1) if match else '', payload))
            researcher_payloads = [payload for role, payload in failure_payloads if role == 'researcher']
            assert [payload.get('phase') for payload in researcher_payloads] == ['act', 'act', 'draft'], researcher_payloads
            reporter_payloads = [payload for role, payload in failure_payloads if role == 'reporter']
            assert len(reporter_payloads) == 1 and reporter_payloads[0].get('evidence') == [], reporter_payloads

            print('Simulated HTTP: quick mode stayed direct despite a contradictory dispatcher, with zero searches.')
            print('Simulated HTTP: deep mode researched a greeting through background search and reporting.')
            print('Simulated HTTP: API web search, background filtering, web evidence and cited report passed.')
            print('Research budgets: dense results are capped, decisions have generation limits, and reporter format repair preserves cited evidence without a second call.')
            print('Context cutoff: an over-budget decision is skipped and the worker goes from draft to report without any further retrieval.')
            print('Thinking-budget conflict: a streaming HTTP 400 is recovered once and the worker saves a cited report.')
            print('Live SSE: reporter delta arrived while the worker was running; tool reason and reconnect cursor passed.')
            print('Reporter citation rejection: report and CSV preserve valid provisional evidence.')
            print('Provider failure: rewritten search retries stop after two consecutive failures and produce an explicit no-evidence report.')
            print('No real search provider or remote web page was contacted.')
    finally:
        try:
            stopped = stop_processes(processes)
        finally:
            if fixture is not None:
                fixture.shutdown()
                fixture.server_close()
            if fixture_thread is not None:
                fixture_thread.join(timeout=5)
            if sink is not None:
                sink.close()
            temporary.cleanup()
        if not stopped or (fixture_thread is not None and fixture_thread.is_alive()):
            raise RuntimeError('Acceptance subprocess or fixture HTTP server did not stop cleanly')


if __name__ == '__main__':
    main()
