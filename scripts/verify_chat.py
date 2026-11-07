"""Isolated HTTP acceptance using a simulated OpenAI-compatible model, not a real model."""
import io, json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[1]

class ModelFixture(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args): pass
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        ModelFixture.requests.append(req)
        prompt = req['messages'][0]['content']
        data = json.loads(req['messages'][1]['content'])
        if '你是研究主控' in prompt:
            if not data['results']:
                action = 'web_research' if data['source'] == 'web' else 'paper_research'
                result = {'action': action, 'task': 'accuracy'}
            elif data['review'] is None:
                result = {'action': 'review'}
            else:
                result = {'action': 'report'}
        elif '你是研究专家' in prompt:
            if not data['recent_result']:
                result = {'action': 'search', 'query': 'accuracy'}
            elif data['candidates'] and not data['evidence']:
                result = {'action': 'read', 'candidate_ids': [data['candidates'][0]['id']]}
            else:
                result = {'action': 'finish'}
        elif '你是无工具研究审查者' in prompt:
            result = {'sufficient': True, 'gaps': [], 'summary': '已审查'}
        elif '只写有对应证据' in prompt or '你是报告修订者' in prompt:
            evidence = data['evidence']
            result = {'claims':[{'text':'准确率为 92%','citations':[evidence[0]['id']]}, {'text':'成本降低 30%','citations':[evidence[0]['id']]}] if evidence else [], 'limitations':'成本数据不足'}
        elif '严格核查' in prompt:
            result = {'supported':True}
        else:
            self.send_error(500); return
        raw = json.dumps({'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)}}]}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)

def make_pdf():
    writer=PdfWriter(); page=writer.add_blank_page(width=300,height=200)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 100 Td (accuracy was 92%) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    out=io.BytesIO(); writer.write(out); return out.getvalue()

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]

def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),ModelFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    processes=[]
    temporary = tempfile.TemporaryDirectory(prefix='tufa-chat-')
    directory = temporary.name
    try:
        env=os.environ.copy()
        for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL','OPENALEX_API_KEY'): env.pop(key,None)
        env['TUFA_DATA_DIR']=directory
        port=int(os.environ.get('TUFA_VERIFY_API_PORT','0')) or free_port(); base=f'http://127.0.0.1:{port}'
        with open(os.devnull,'wb') as sink:
            api_app = 'scripts.fixture_api:app' if '--paper-fixture' in sys.argv else 'apps.api.main:app'
            for args in ([sys.executable,'-m','uvicorn',api_app,'--host','127.0.0.1','--port',str(port)], [sys.executable,'-m','apps.worker']):
                processes.append(subprocess.Popen(args,cwd=ROOT,env=env,stdout=sink,stderr=sink))
            with httpx.Client(base_url=base,timeout=10,trust_env=False) as c:
                for _ in range(100):
                    try:
                        if c.get('/health').status_code == 200: break
                    except httpx.RequestError: pass
                    time.sleep(.2)
                else: raise AssertionError('API failed to start')
                def call(method,path,**kw):
                    r=c.request(method,path,**kw); r.raise_for_status(); return r.json()
                def wait(run_id):
                    for _ in range(150):
                        t=call('GET',f'/runs/{run_id}')
                        if t['status'] in ('succeeded','failed','interrupted'): return t
                        time.sleep(.2)
                    raise AssertionError('worker timed out')
                p=call('POST','/projects',json={'name':'fixture'})['id']
                if '--paper-fixture' in sys.argv:
                    found=call('GET',f'/projects/{p}/paper-search',params={'q':'attention','source':'openalex'})
                    assert found['results'][0]['doi']=='https://doi.org/10.1234/ATTENTION'
                    print('Simulated external paper search verified', flush=True)
                pdf=make_pdf()
                paper=call('POST',f'/projects/{p}/papers/import',data={'title':'accuracy research'},files={'file':('fixture.pdf',pdf,'application/pdf')})
                q=call('POST',f'/projects/{p}/questions',json={'question':'accuracy and cost?', 'source':'local'})
                failed=wait(q['run_id']); assert failed['status']=='failed' and '配置' in failed['error'], failed
                call('PUT','/settings',json={'openai_base_url':f'http://127.0.0.1:{server.server_port}/v1','openai_model':'fixture'})
                retry=call('POST',f"/runs/{q['run_id']}/retry")
                completed=wait(retry['id']); assert completed['status']=='succeeded',completed
                result=completed['result']; assert '证据不足' in result['content'] and '成本降低 30%' not in result['content']
                evidence=result['evidence']; assert len(evidence)==1 and evidence[0]['page']==1 and evidence[0]['version_id']==paper['versions'][0]['id']
                assert c.get(paper['versions'][0]['file_url']).content==pdf
                report=result['report_id']; assert '92%' in c.get(f'/reports/{report}/markdown').text
                assert 'accuracy was 92%' in c.get(f'/reports/{report}/evidence.csv').text
                assert len(call('GET',f"/conversations/{q['conversation_id']}/messages"))==2
                assert call('GET',f'/projects/{p}/search',params={'q':'accuracy'})['papers']
                outside=call('POST',f'/projects/{p}/papers',json={'title':'OUTSIDE_SECRET','abstract':'OUTSIDE_SECRET content'})
                prior=call('POST',f'/projects/{p}/questions',json={'question':'OUTSIDE_SECRET','conversation_id':q['conversation_id'],'source':'local'})
                assert wait(prior['run_id'])['status']=='succeeded'
                collection=call('POST',f'/projects/{p}/collections',json={'name':'fixture collection'})['id']
                path=f'/projects/{p}/collections/{collection}/papers'
                call('POST',path,json={'paper_id':paper['id']})
                ModelFixture.requests.clear()
                selected=call('POST',f'/projects/{p}/questions',json={'question':'accuracy','source':'collection','collection_id':collection,'conversation_id':q['conversation_id']})
                call('POST',path,json={'paper_id':outside['id']})
                assert c.delete(path+'/'+str(paper['id'])).status_code==204
                selected_task=wait(selected['run_id'])
                assert selected_task['paper_ids']==[paper['id']], selected_task
                assert selected_task['status']=='succeeded', selected_task
                assert all(e['paper_id']==paper['id'] for e in selected_task['result']['evidence'])
                assert selected_task['result']['evidence'], selected_task
                assert 'OUTSIDE_SECRET' not in json.dumps(ModelFixture.requests,ensure_ascii=False)
                print('Collection snapshot and model isolation checks passed', flush=True)
                print('Simulated model: API, worker, evidence, retry and report checks passed', flush=True)
                if '--serve' in sys.argv:
                    print(f'API: {base}  isolated data: {directory}', flush=True)
                    print('Simulated model is active; Ctrl+C stops the fixture.', flush=True)
                    if '--paper-fixture' in sys.argv: print('Simulated external paper search is active.', flush=True)
                    try:
                        while True: time.sleep(1)
                    except KeyboardInterrupt: pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        server.shutdown(); server.server_close()
        temporary.cleanup()

if __name__=='__main__': main()
