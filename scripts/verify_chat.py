"""Verify chat through the real API and worker with a local model fixture only."""

import io
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
            'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENALEX_API_KEY',
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
            })
            failed = wait(pending['run_id'])
            assert failed['status'] == 'failed' and '配置模型' in failed.get('error', ''), failed

            call('PUT', '/settings', json={
                'openai_base_url': f'http://127.0.0.1:{fixture.server_port}/v1',
                'openai_model': 'fixture-default',
            })
            retry = call('POST', f"/runs/{pending['run_id']}/retry")
            completed = wait(retry['id'])
            assert completed['status'] == 'succeeded', completed
            result = completed['result']
            assert '92%' in result['content']
            assert result['evidence'] and result['evidence'][0]['page'] == 1
            assert result['evidence'][0]['version_id'] == imported['versions'][0]['id']
            assert completed['stage'] == 'completed'
            assert stages(retry['id'])[-1:] == ['reporter'], stages(retry['id'])
            assert 'accuracy-fixture.pdf' in str([
                item['messages'][0]['content'] for item in fixture.model_requests
                if '运行节点标识：planner' in item['messages'][0]['content']
            ])
            assert all(item['model'] == 'fixture-selected' for item in fixture.model_requests)
            assert client.get(imported['versions'][0]['file_url']).content == pdf
            report_id = result['report_id']
            assert '92%' in client.get(f'/reports/{report_id}/markdown').text
            csv_text = client.get(f'/reports/{report_id}/evidence.csv').text
            assert 'accuracy was 92%' in csv_text

            # A direct response runs only the dispatcher and does not search.
            before = len(fixture.model_requests)
            direct = call('POST', f'/projects/{project}/questions', json={'question': 'direct: define accuracy', 'source': 'auto'})
            direct_task = wait(direct['run_id'])
            direct_requests = fixture.model_requests[before:]
            assert direct_task['status'] == 'succeeded', direct_task
            assert direct_task['stage'] == 'completed'
            assert direct_task['result']['evidence'] == []
            roles = [
                line.split('运行节点标识：', 1)[1].splitlines()[0].strip()
                for item in direct_requests
                for line in item['messages'][0]['content'].splitlines()
                if '运行节点标识：' in line
            ]
            assert roles == ['dispatcher'], roles
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
            print('Simulated model: API, worker, failed-task retry, direct answer, PDF evidence, report and empty collection passed.')
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
