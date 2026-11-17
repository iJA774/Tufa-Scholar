"""Offline real-process acceptance: conversion -> project FTS -> agent -> cited report."""
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
            for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL','OPENALEX_API_KEY','SEARCH_API','SEARX_HOST','TAVILY_API_KEY','INFOQUEST_API_KEY','BRAVE_SEARCH_API_KEY','SERPER_API_KEY'):
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
                pdf = make_pdf()
                batch = call('POST','/conversion-batches',json={'root_name':'Research','project_id':project,'files':[{'path':'sub/accuracy.pdf','size':len(pdf)},{'path':'notes.txt','size':11}]})
                for item,data in zip(batch['files'],(pdf,b'local notes')):
                    call('POST',f'/conversion-batches/{batch["id"]}/files/{item["id"]}',files={'file':('input',data)})
                call('POST',f'/conversion-batches/{batch["id"]}/start')
                converted = wait(f'/conversion-batches/{batch["id"]}','state',{'completed','cancelled','interrupted'})
                assert converted['counts']['succeeded']==2, converted
                file_id = converted['files'][0]['id']
                preview = call('GET',f'/conversion-batches/{batch["id"]}/files/{file_id}/preview')
                assert 'accuracy was 92%' in preview['markdown'] and preview['mapping'][0]['page']==1
                zipped = client.get(f'/conversion-batches/{batch["id"]}/zip')
                assert zipped.status_code==200 and zipped.content.startswith(b'PK')
                call('PUT','/settings',json={'openai_base_url':f'http://127.0.0.1:{fixture.server_port}/v1','openai_model':'fixture-default'})
                question = call('POST',f'/projects/{project}/questions',json={'question':'accuracy','source':'local','research_mode':'deep','research_depth':'light','relevance_filter':'relaxed'})
                task = wait(f'/runs/{question["run_id"]}','status',{'succeeded','failed','interrupted'})
                assert task['status']=='succeeded',task
                evidence = task['result']['evidence']
                assert any(e.get('page')==1 and 'accuracy was 92%' in e['text'] and e.get('paper_id')==converted['files'][0]['paper_id'] for e in evidence), evidence
                report = client.get(f'/reports/{task["result"]["report_id"]}/markdown')
                assert report.status_code==200 and '92%' in report.text
                assert fixture.model_requests
                print(json.dumps({'conversion':'completed','files':2,'model':'local fixture only','research':'succeeded','cited_pdf_page':1,'model_calls':len(fixture.model_requests)},ensure_ascii=False))
                print('Original PDF -> offline Markdown -> project search -> single worker -> page-grounded report passed.')
        finally:
            stopped = stop_processes(processes)
            if fixture: fixture.shutdown(); fixture.server_close()
            if fixture_thread: fixture_thread.join(timeout=5)
            if not stopped: raise RuntimeError('Acceptance processes did not stop')


if __name__ == '__main__': main()
