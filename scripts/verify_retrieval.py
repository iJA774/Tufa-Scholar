"""Exercise the running API and worker with an isolated TUFA_DATA_DIR."""
import io
import os
import time
import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

base = os.environ.get('TUFA_API_URL', 'http://127.0.0.1:8000').rstrip('/')
client = httpx.Client(base_url=base, timeout=10)

def call(method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()

def pdf(width):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()

def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 100 Td (Photosynthesis evidence) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

project = call('POST', '/projects', json={'name': '验收项目'})['id']
other = call('POST', '/projects', json={'name': '隔离项目'})['id']
a = call('POST', f'/projects/{project}/conversations', json={'title': 'A'})['id']
b = call('POST', f'/projects/{project}/conversations', json={'title': 'B'})['id']
assert a != b
path = f'/projects/{project}/papers/import'
def upload(contents, target=path):
    return call('POST', target, data={'title': 'Quantum Benchmark', 'doi': 'https://doi.org/10.1234/TEST'}, files={'file': ('paper.pdf', contents, 'application/pdf')})
first = upload(pdf(200))
repeat = upload(pdf(200))
second = upload(pdf(210))
assert first['id'] == repeat['id'] == second['id'] and repeat['duplicate']
assert len(second['versions']) == 2
assert second['versions'][0]['text_status'] == 'no_extractable_text'
assert client.get(second['versions'][0]['file_url']).content == pdf(200)
assert len(call('GET', f'/projects/{project}/papers')) == 1
assert call('GET', f'/projects/{other}/papers') == []
assert upload(pdf(200), f'/projects/{other}/papers/import')['id'] == first['id']
assert len(call('GET', f'/projects/{other}/papers')) == 1
text_paper = call('POST', path, data={'title':'Unrelated title'}, files={'file':('text.pdf', text_pdf(), 'application/pdf')})
assert text_paper['versions'][0]['text_status'] == 'text_available'
run = call('POST', f'/projects/{project}/runs', json={'query':'Photosynthesis', 'source':'local', 'conversation_id':b})['run_id']
for _ in range(100):
    task = call('GET', f'/runs/{run}')
    if task['status'] in ('succeeded','failed','interrupted'):
        break
    time.sleep(.2)
assert task['status'] == 'succeeded', task
assert any(p['id'] == text_paper['id'] for p in task['result']['results']), task
assert any(item['id'] == run for item in call('GET', f'/projects/{project}/runs'))
assert client.get('/projects/999999/papers').status_code == 404
assert client.post(f'/projects/{project}/runs', json={'query':'x', 'source':'bad'}).status_code == 422
print('HTTP API + worker verification passed:', task['status'])
