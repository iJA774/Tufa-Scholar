"""Verify the running API against an explicitly isolated data directory, with worker stopped."""
import io
import os
from pathlib import Path
import sqlite3

import httpx
from pypdf import PdfWriter


root = Path(__file__).resolve().parents[1]
directory = os.environ.get('TUFA_DATA_DIR')
if os.environ.get('TUFA_VERIFY_ISOLATED') != '1' or not directory:
    raise SystemExit('Set TUFA_VERIFY_ISOLATED=1 and TUFA_DATA_DIR to the isolated API data directory; stop the worker.')
data = Path(directory)
if not data.is_absolute():
    data = root / data
data = data.resolve()
if data == (root / 'data').resolve():
    raise SystemExit('The default data directory cannot be used for this verification.')

client = httpx.Client(base_url=os.environ.get('TUFA_API_URL', 'http://127.0.0.1:8000').rstrip('/'), timeout=10)


def call(method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        raise AssertionError(f'{method} {path}: expected {expected}, got {response.status_code}')
    return response


db_path = Path(call('GET', '/health').json()['database']['path']).resolve()
if db_path != data / 'research.db':
    raise SystemExit('Running API data directory differs from TUFA_DATA_DIR.')
initial = call('GET', '/settings').json()
if (initial['openalex']['key_configured'] or initial['model']['key_configured']
        or initial['model']['base_url'] or initial['model']['model']):
    raise SystemExit('Use an isolated API with empty settings and no configuration environment variables.')

dummy_alex = 'dummy-openalex-integration-only'
dummy_model = 'dummy-model-integration-only'
try:
    response = call('PUT', '/settings', json={
        'openalex_api_key': dummy_alex, 'openai_api_key': dummy_model,
        'openai_base_url': 'https://example.invalid/v1', 'openai_model': 'test-model',
    })
    assert dummy_alex not in response.text and dummy_model not in response.text
    current = call('GET', '/settings')
    assert dummy_alex not in current.text and dummy_model not in current.text
    value = current.json()
    assert value['openalex']['key_configured'] and value['openalex']['key_source'] == 'local'
    assert value['model']['key_configured'] and value['model']['key_source'] == 'local'
    assert value['model']['base_url'] == 'https://example.invalid/v1'
    assert value['model']['model'] == 'test-model'
    assert value['model']['base_url_source'] == 'local' and value['model']['model_source'] == 'local'
    value = call('PUT', '/settings', json={'openalex_api_key': '', 'openai_api_key': ''}).json()
    assert value['openalex']['key_configured'] and value['model']['key_configured']

    project = call('POST', '/projects', expected=201, json={'name': '删除验收空间'}).json()['id']
    other = call('POST', '/projects', expected=201, json={'name': '共享验收空间'}).json()['id']
    conversation = call('POST', f'/projects/{project}/conversations', expected=201, json={'title': '验收对话'}).json()['id']
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    content = output.getvalue()
    def upload(project_id):
        return call('POST', f'/projects/{project_id}/papers/import', expected=201,
                    data={'title': '共享验收文献'}, files={'file': ('test.pdf', content, 'application/pdf')}).json()
    first = upload(project)
    shared = upload(other)
    assert first['id'] == shared['id']
    file_url = first['versions'][0]['file_url']
    run = call('POST', f'/projects/{project}/runs', expected=201,
               json={'query': 'test', 'source': 'local', 'conversation_id': conversation}).json()['run_id']
    assert call('GET', f'/runs/{run}').json()['status'] == 'pending', 'Stop the worker before verification.'
    # Simulate an already claimed task in this isolated DB; no worker is running.
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE tasks SET status='running', stage='searching' WHERE id=? AND status='pending'", (run,))
    call('DELETE', f'/projects/{project}', expected=409)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE tasks SET status='pending', stage='pending' WHERE id=? AND status='running'", (run,))
    call('DELETE', f'/projects/{project}', expected=204)
    call('DELETE', f'/projects/{project}', expected=404)
    call('GET', f'/projects/{project}/conversations', expected=404)
    call('GET', f'/runs/{run}', expected=404)
    assert any(p['id'] == first['id'] for p in call('GET', f'/projects/{other}/papers').json())
    assert call('GET', file_url).content == content
finally:
    call('PUT', '/settings', json={'clear_openalex_api_key': True, 'clear_openai_api_key': True,
                                  'openai_base_url': '', 'openai_model': ''})

cleaned = call('GET', '/settings').json()
assert not cleaned['openalex']['key_configured'] and not cleaned['model']['key_configured']
assert not cleaned['model']['base_url'] and not cleaned['model']['model']
print('Workspace deletion and settings HTTP verification passed.')
