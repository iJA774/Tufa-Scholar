"""Verify chat through the real API and worker with a local model fixture only."""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts.verification_support import ROOT, api_commands, assert_isolated_services, free_port, start_fixture_server, stop_processes


def make_pdf():
    """Return a tiny extractable PDF for API/worker evidence acceptance."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    })
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 100 Td (accuracy was 92%) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def main():
    temporary = tempfile.TemporaryDirectory(prefix='tufa-chat-verify-')
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
        for key in (
            'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENALEX_API_KEY', 'TUFA_PREPROCESSER_MODEL',
            'SEARCH_API', 'SEARX_HOST', 'TAVILY_API_KEY', 'INFOQUEST_API_KEY',
            'BRAVE_SEARCH_API_KEY', 'SERPER_API_KEY',
        ):
            env.pop(key, None)
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

            def reported_tokens(run_id):
                log_path = Path(temporary.name) / 'logs' / 'worker.log'
                pattern = rf'model_call (?:completed|invalid) run_id={run_id} [^\n]*\btotal_tokens=(\d+)'
                return sum(int(value) for value in re.findall(pattern, log_path.read_text(encoding='utf-8')))

            project = call('POST', '/projects', json={'name': 'Isolated chat acceptance'})['id']
            pdf = make_pdf()
            imported = call(
                'POST', f'/projects/{project}/papers/import',
                data={'title': 'Accuracy fixture study'},
                files={'file': ('accuracy-fixture.pdf', pdf, 'application/pdf')},
            )

            # Exercise the real failed-task/retry path before configuring a model.
            pending = call('POST', f'/projects/{project}/questions', json={
                'question': 'accuracy', 'source': 'local', 'model_name': 'fixture-selected',
                'research_mode': 'deep', 'report_format': 'brief',
                'research_depth': 'light', 'prefer_primary_evidence': False, 'network_search': False,
                'relevance_filter': 'relaxed',
            })
            failed = wait(pending['run_id'])
            assert failed['status'] == 'failed' and '配置模型' in failed.get('error', ''), failed
            failed_detail = call('GET', f"/runs/{pending['run_id']}")
            assert failed_detail['conversation_total_tokens'] is None
            assert failed_detail['research_mode'] == 'deep', failed_detail
            preferences = {
                'research_depth': 'light',
                'report_format': 'brief',
                'prefer_primary_evidence': False,
                'relevance_filter': 'relaxed',
                'network_search': False,
            }
            assert {key: failed_detail[key] for key in preferences} == preferences, failed_detail

            call('PUT', '/settings', json={
                'openai_base_url': f'http://127.0.0.1:{fixture.server_port}/v1',
                'openai_model': 'fixture-default',
            })
            originals = Path(temporary.name) / 'documents' / 'originals'
            originals.mkdir(parents=True, exist_ok=True)
            (originals / 'accuracy-fixture.pdf').write_bytes(pdf)
            batch = call('POST', '/conversion-batches/prepare', json={'project_id': project})
            for _ in range(150):
                batch = call('GET', f"/conversion-batches/{batch['id']}")
                if batch['state'] in {'completed', 'cancelled', 'interrupted'}:
                    break
                time.sleep(0.2)
            assert batch['state'] == 'completed', batch
            model_requests_before_retry = len(fixture.model_requests)
            retry = call('POST', f"/runs/{pending['run_id']}/retry")
            assert retry['research_mode'] == 'deep', retry
            assert {key: retry[key] for key in preferences} == preferences, retry
            completed = wait(retry['id'])
            assert completed['status'] == 'succeeded', completed
            assert completed['research_mode'] == 'deep', completed
            assert {key: completed[key] for key in preferences} == preferences, completed
            result = completed['result']
            assert '92%' in result['content']
            cited = result['evidence'][0]
            assert cited['source_level'] == 'local_markdown' and cited['char_end'] > cited['char_start'], cited
            assert cited['paper_id'] == imported['id'], cited
            assert completed['stage'] == 'completed'
            initial_usage = reported_tokens(retry['id'])
            assert initial_usage > 0 and completed['conversation_total_tokens'] == initial_usage
            retry_stages = stages(retry['id'])
            assert retry_stages[-1:] == ['reporter'], retry_stages
            assert retry_stages[:2] == ['dispatcher', 'planner'] and 'background_investigator' not in retry_stages, retry_stages
            activity = client.get(f"/runs/{retry['id']}/events")
            activity.raise_for_status()
            assert 'background_investigator' not in activity.text
            worker_log = (Path(temporary.name) / 'logs' / 'worker.log').read_text(encoding='utf-8', errors='replace')
            usage_events = [line for line in worker_log.splitlines()
                            if f'run_id={retry["id"]} ' in line and 'model_call completed ' in line]
            started_events = [line for line in worker_log.splitlines()
                              if f'run_id={retry["id"]} ' in line and 'model_call started ' in line]
            retry_model_requests = [item for item in fixture.model_requests
                                    if json.loads(item['messages'][1]['content']).get('question') == 'accuracy']
            assert len(usage_events) == len(retry_model_requests), (len(usage_events), len(retry_model_requests))
            assert all('prompt_id=' in line and 'attempt=1' in line and 'prompt_tokens=' in line
                       and 'completion_tokens=' in line and 'total_tokens=' in line
                       for line in usage_events), usage_events
            assert len(started_events) == len(retry_model_requests), (len(started_events), len(retry_model_requests))
            assert all('system_chars=' in line and 'user_chars=' in line and 'input_chars=' in line
                       for line in started_events), started_events
            retry_roles = [
                line.split('运行节点标识：', 1)[1].splitlines()[0].strip()
                for item in fixture.model_requests
                if json.loads(item['messages'][1]['content']).get('question') == 'accuracy'
                for line in item['messages'][0]['content'].splitlines()
                if '运行节点标识：' in line
            ]
            reporter_prompts = [item['messages'][0]['content'] for item in retry_model_requests
                                if '运行节点标识：reporter' in item['messages'][0]['content']]
            assert len(reporter_prompts) == 1 and '### 决策简报' in reporter_prompts[0], reporter_prompts
            assert retry_roles and retry_roles[0] == 'planner', retry_roles
            assert 'background_investigator' not in retry_roles and 'dispatcher' not in retry_roles, retry_roles
            local_requests = [json.loads(item['messages'][1]['content']) for item in retry_model_requests]
            actions = [item for item in local_requests if item.get('phase') == 'act']
            first_action = actions[0]
            assert first_action['local_domains'] == ['模型评估']
            assert not first_action['evidence'] and not first_action['tool_results']
            dossier = next(result for result in actions[1]['tool_results'] if result['tool'] == 'select_local_domains')
            assert dossier['documents'][0]['title'] and dossier['documents'][0]['tags']
            assert all('text' not in document and 'content' not in document for document in dossier['documents'])
            assert not actions[1]['evidence']
            local_tools = [request['tools'] for request in retry_model_requests if request.get('tools')]
            assert local_tools and all(
                {tool['function']['name'] for tool in tools} == {'select_local_domains', 'read_local_markdown'}
                for tools in local_tools
            )
            assert all('local_papers' not in item for item in local_requests)
            assert all(item['model'] == 'fixture-selected' for item in fixture.model_requests[model_requests_before_retry:])
            assert client.get(imported['versions'][0]['file_url']).content == pdf
            report_id = result['report_id']
            assert '92%' in client.get(f'/reports/{report_id}/markdown').text
            csv_text = client.get(f'/reports/{report_id}/evidence.csv').text
            assert 'accuracy was 92%' in csv_text

            # A direct response runs only the dispatcher and does not search.
            before = len(fixture.model_requests)
            direct = call('POST', f'/projects/{project}/questions', json={'question': 'direct: define accuracy', 'source': 'auto', 'report_format': 'explainer'})
            direct_task = wait(direct['run_id'])
            direct_requests = fixture.model_requests[before:]
            assert direct_task['status'] == 'succeeded', direct_task
            assert direct_task['stage'] == 'completed'
            assert direct_task['result']['evidence'] == []
            assert direct_task['conversation_total_tokens'] == reported_tokens(direct['run_id']) > 0
            roles = [
                line.split('运行节点标识：', 1)[1].splitlines()[0].strip()
                for item in direct_requests
                for line in item['messages'][0]['content'].splitlines()
                if '运行节点标识：' in line
            ]
            assert roles == ['dispatcher'], roles
            assert '通俗语言解释核心概念' in direct_requests[0]['messages'][0]['content']
            assert stages(direct['run_id']) == ['dispatcher'], stages(direct['run_id'])

            # A blank collection still finishes with an explicit evidence gap.
            empty_project = call('POST', '/projects', json={'name': 'Empty collection acceptance'})['id']
            collection = call('POST', f'/projects/{empty_project}/collections', json={'name': 'empty'})['id']
            empty = call('POST', f'/projects/{empty_project}/questions', json={
                'question': 'no-evidence', 'source': 'collection', 'collection_id': collection,
            })
            empty_task = wait(empty['run_id'])
            assert empty_task['status'] == 'succeeded', empty_task
            assert empty_task['result']['evidence'] == []
            assert '没有可核查证据' in empty_task['result']['content'] or '证据不足' in empty_task['result']['content']
            assert empty_task['stage'] == 'completed'
            assert stages(empty['run_id'])[-1:] == ['reporter'], stages(empty['run_id'])

            messages = call('GET', f"/conversations/{pending['conversation_id']}/messages")
            assert len(messages) == 2
            # Regeneration uses the current model and the original question/scope,
            # while retaining the old report and avoiding a duplicate user message.
            before = len(fixture.model_requests)
            regenerated = call('POST', f"/runs/{retry['id']}/regenerate", json={'model_name': 'fixture-replacement'})
            assert regenerated['origin_run_id'] == pending['run_id']
            assert {key: regenerated[key] for key in preferences} == preferences
            regenerated_task = wait(regenerated['id'])
            assert regenerated_task['status'] == 'succeeded', regenerated_task
            assert regenerated_task['result']['evidence']
            regeneration_usage = reported_tokens(regenerated['id'])
            assert regeneration_usage > 0
            total_usage = initial_usage + regeneration_usage
            assert regenerated_task['conversation_total_tokens'] == total_usage
            history_runs = call('GET', f"/projects/{project}/runs", params={'conversation_id': pending['conversation_id']})
            assert all(run['conversation_total_tokens'] == total_usage for run in history_runs)
            assert call('GET', f"/runs/{direct['run_id']}")['conversation_total_tokens'] == direct_task['conversation_total_tokens']
            assert all(request['model'] == 'fixture-replacement' for request in fixture.model_requests[before:])
            messages = call('GET', f"/conversations/{pending['conversation_id']}/messages")
            assert [message['role'] for message in messages] == ['user', 'assistant', 'assistant']
            assert client.get(f'/reports/{report_id}/markdown').status_code == 200
            print('Simulated model: API/worker, retry and regeneration, cumulative session tokens, direct answer, Markdown evidence, report and empty collection passed.')
            print('Cloud model and real-provider quality were not exercised.')
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
