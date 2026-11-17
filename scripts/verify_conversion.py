"""Real-process acceptance: prepare local documents and cite Markdown via FTS5."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
import httpx
from scripts.verification_support import ROOT, api_commands, assert_isolated_services, free_port, start_fixture_server, stop_processes
from scripts.verify_chat import make_pdf


def main():
    processes, fixture, fixture_thread = [], None, None
    with tempfile.TemporaryDirectory(prefix='tufa-conversion-verify-') as temporary, open(os.devnull, 'wb') as sink:
        try:
            fixture, fixture_thread = start_fixture_server()
            env = os.environ.copy()
            for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL','OPENALEX_API_KEY','TUFA_PREPROCESSER_MODEL','SEARCH_API','SEARX_HOST','TAVILY_API_KEY','INFOQUEST_API_KEY','BRAVE_SEARCH_API_KEY','SERPER_API_KEY'):
                env.pop(key, None)
            env['TUFA_DATA_DIR'] = temporary
            port = free_port()
            for command in api_commands(port):
                processes.append(subprocess.Popen(command,cwd=ROOT,env=env,stdout=sink,stderr=sink))
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=20,trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get('/health').status_code == 200: break
                    except httpx.RequestError: pass
                    time.sleep(.1)
                else: raise AssertionError('Isolated API did not start')
                assert_isolated_services(client,processes,temporary)
                def call(method,path,**kwargs):
                    response = client.request(method,path,**kwargs)
                    response.raise_for_status()
                    return response.json()
                def wait(path,field,states):
                    deadline = time.monotonic()+60
                    while time.monotonic()<deadline:
                        result = call('GET',path)
                        if result[field] in states: return result
                        time.sleep(.1)
                    raise AssertionError('Acceptance deadline exceeded')
                project = call('POST','/projects',json={'name':'Converted PDF research'})['id']
                originals = Path(temporary) / 'documents' / 'originals'
                (originals / 'sub').mkdir(parents=True)
                (originals / 'sub' / 'accuracy.pdf').write_bytes(make_pdf())
                (originals / 'notes.txt').write_text('local notes', encoding='utf-8')
                call('PUT','/settings',json={'openai_base_url':f'http://127.0.0.1:{fixture.server_port}/v1','openai_model':'fixture-default'})
                batch = call('POST','/conversion-batches/prepare',json={'project_id':project})
                converted = wait(f'/conversion-batches/{batch["id"]}','state',{'completed','cancelled','interrupted'})
                pdf_item = next(item for item in converted['files'] if item.get('format') == 'pdf')
                classified = [item for item in converted['files'] if item.get('classification')]
                assert len(classified) == 2, converted
                preview = call('GET',f'/conversion-batches/{batch["id"]}/files/{pdf_item["id"]}/preview')
                assert 'accuracy was 92%' in preview['markdown'] and preview['mapping'][0]['page']==1
                zipped = client.get(f'/conversion-batches/{batch["id"]}/zip')
                assert zipped.status_code==200 and zipped.content.startswith(b'PK')
                question = call('POST',f'/projects/{project}/questions',json={'question':'accuracy','source':'local','research_mode':'deep','research_depth':'light','relevance_filter':'relaxed'})
                task = wait(f'/runs/{question["run_id"]}','status',{'succeeded','failed','interrupted'})
                assert task['status']=='succeeded',task
                evidence = task['result']['evidence']
                cited = next(item for item in evidence if item['source_level'] == 'local_markdown')
                assert cited['paper_id'] == pdf_item['paper_id'], cited
                assert preview['markdown'][cited['char_start']:cited['char_end']] == cited['text'], cited
                assert 'accuracy was 92%' in cited['text']
                report = client.get(f'/reports/{task["result"]["report_id"]}/markdown')
                assert report.status_code==200 and '92%' in report.text
                assert fixture.model_requests
                print(json.dumps({'preprocessing':'completed','classified_files':len(classified),'model':'local fixture only','research':'succeeded','source':cited['source_level'],'model_calls':len(fixture.model_requests)},ensure_ascii=False))
                print('Original PDF -> Markdown -> domain dossier -> FTS5 read -> Markdown-grounded report passed.')
        finally:
            stopped = stop_processes(processes)
            if fixture: fixture.shutdown(); fixture.server_close()
            if fixture_thread: fixture_thread.join(timeout=5)
            if not stopped: raise RuntimeError('Acceptance processes did not stop')


if __name__ == '__main__': main()
