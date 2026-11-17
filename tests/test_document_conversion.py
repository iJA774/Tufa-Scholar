import io
import json
import os
import tempfile
import time
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi.testclient import TestClient
from apps.api.main import app
from src.literature.conversion.converter import convert_file, process_file
from src.literature.conversion.models import ConversionError, MAX_FILE
from src.persistence import conversions as store, repository as repo
from src.persistence.storage import connect_database
from tests.conversion_fixtures import pdf_fixture, docx_fixture


def slow_conversion(*args):
    """Importable spawn target to exercise actual process cancellation."""
    time.sleep(10)


class ConverterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def convert(self, name, data, encoding='auto'):
        path = self.root/name; path.write_bytes(data)
        original = sha256(data).hexdigest()
        value = convert_file(path, name, encoding)
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), original)
        return value

    def test_pdf_structure_pages_and_provenance(self):
        markdown, meta = self.convert('ordinary.pdf', pdf_fixture())
        self.assertIn('# Synthetic Research Title', markdown)
        self.assertIn('## 1 Introduction', markdown)
        self.assertNotIn('Repeated running header', markdown)
        self.assertEqual(len(meta['pages']), 2)
        self.assertIn('<!-- page:2 -->', markdown)
        for mapping in meta['mapping']:
            self.assertTrue(markdown[mapping['start']:mapping['end']].strip())
            self.assertIn('Traceable quantum evidence', meta['pages'][mapping['page']-1])

    def test_pdf_columns_tables_and_formula(self):
        markdown, _ = self.convert('columns.pdf', pdf_fixture('columns'))
        self.assertLess(markdown.index('LEFT_4'), markdown.index('RIGHT_0'))
        markdown, _ = self.convert('table.pdf', pdf_fixture('table'))
        self.assertIn('| Method | Score |', markdown)
        self.assertIn('| Baseline | 0.95 |', markdown)
        markdown, meta = self.convert('math.pdf', pdf_fixture('math'))
        self.assertIn('E = mc^2', markdown)
        self.assertIn('Original source citation', markdown)
        self.assertTrue(any('公式' in text for text in meta['warnings']))

    def test_docx_structure_and_images(self):
        markdown, meta = self.convert('word.docx', docx_fixture(True))
        for text in ('# Synthetic Word Title', '# Methods', '**bold**', '*italic*', '- Bullet point', '1. First numbered', '2. Second numbered', '| alpha | 42 |', '[图片：未进行 OCR]'):
            self.assertIn(text, markdown)
        self.assertLess(markdown.index('| alpha'), markdown.index('Last paragraph'))
        self.assertTrue(meta['partial'])

    def test_text_encodings_and_literal_headings(self):
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16', 'gb18030'):
            text = '本地文档测试，保持段落和原文信息。\n\n# 普通文本\n'
            markdown, _ = self.convert('notes.txt', text.encode(encoding), 'auto' if encoding != 'gb18030' else encoding)
            self.assertIn('本地文档测试', markdown)
            self.assertIn('\\# 普通文本', markdown)
        with self.assertRaises(ConversionError): self.convert('binary.txt', b'\x00\x01\x02')

    def test_empty_corrupt_encrypted_and_scan(self):
        for name, data, code in [('empty.txt', b'', 'empty_file'), ('bad.pdf', b'%PDF- broken', 'invalid_pdf'), ('wrong.pdf', b'plain', 'format_mismatch'), ('encrypted.pdf',pdf_fixture('encrypted'),'encrypted_pdf'), ('scan.pdf',pdf_fixture('scan'),'scanned_pdf'), ('old.doc',b'\xd0\xcf\x11\xe0','legacy_word'), ('bad.docx',b'PK\x03\x04broken','invalid_docx')]:
            with self.subTest(name=name), self.assertRaises(ConversionError) as caught:
                self.convert(name,data)
            self.assertEqual(caught.exception.code,code)

    def test_docx_hyperlinks_footnotes_and_formula_are_inert(self):
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        document = Document()
        paragraph = document.add_paragraph('Source context ')
        relationship = document.part.relate_to('https://example.invalid/source',RT.HYPERLINK,is_external=True)
        link = OxmlElement('w:hyperlink'); link.set(qn('r:id'),relationship)
        run = OxmlElement('w:r'); text = OxmlElement('w:t'); text.text = 'original link'; run.append(text); link.append(run); paragraph._p.append(link)
        reference = OxmlElement('w:footnoteReference'); reference.set(qn('w:id'),'1'); paragraph._p.append(reference)
        formula = OxmlElement('m:oMath'); math_run = OxmlElement('m:r'); math_text = OxmlElement('m:t'); math_text.text = 'x+y'; math_run.append(math_text); formula.append(math_run); paragraph._p.append(formula)
        stream = io.BytesIO(); document.save(stream)
        out = io.BytesIO()
        with ZipFile(stream) as incoming, ZipFile(out,'w') as zipped:
            for member in incoming.infolist(): zipped.writestr(member,incoming.read(member))
            zipped.writestr('word/footnotes.xml','<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>Original footnote.</w:t></w:r></w:p></w:footnote></w:footnotes>')
        with patch('socket.socket.connect',side_effect=AssertionError('Conversion must stay offline')):
            markdown, meta = self.convert('advanced.docx',out.getvalue())
        self.assertIn('[original link](https://example.invalid/source)',markdown)
        self.assertIn('[^note-1]: Original footnote.',markdown)
        self.assertIn('x+y',markdown)
        self.assertTrue(meta['partial'])

    def test_docx_compression_bomb_and_xml_entity_rejected(self):
        for content, code in [(b'a'*(2*1024*1024),'container_limit'),(b'<!DOCTYPE x [<!ENTITY x "secret">]><x/>','unsafe_xml')]:
            stream = io.BytesIO()
            with ZipFile(stream,'w',compression=ZIP_DEFLATED) as zipped: zipped.writestr('word/document.xml',content)
            with self.assertRaises(ConversionError) as caught: self.convert('bad.docx',stream.getvalue())
            self.assertEqual(caught.exception.code,code)
        stream = io.BytesIO()
        with ZipFile(stream,'w') as zipped: zipped.writestr('word/vbaProject.bin',b'not executed')
        with self.assertRaises(ConversionError) as caught: self.convert('macro.docx',stream.getvalue())
        self.assertEqual(caught.exception.code,'macro_document')

    def test_container_limits_and_disk_error_are_sanitized(self):
        stream = io.BytesIO()
        with ZipFile(stream,'w') as z: z.writestr('../word/document.xml', '<xml/>')
        with self.assertRaises(ConversionError) as caught: self.convert('unsafe.docx', stream.getvalue())
        self.assertEqual(caught.exception.code,'unsafe_container')
        source = self.root/'source'; source.write_text('safe input',encoding='utf-8')
        target = self.root/'result.md'
        original = Path.write_text
        def fail_markdown(path, *args, **kwargs):
            if path.suffix == '.md': raise OSError('secret-user-path')
            return original(path, *args, **kwargs)
        with patch.object(Path,'write_text',fail_markdown), patch('src.literature.conversion.resources.limit_memory'), patch('logging.disable'):
            process_file(str(source),str(target),'x.txt','auto')
        sidecar = target.with_suffix('.json').read_text()
        self.assertIn('storage_error',sidecar)
        self.assertNotIn('secret-user-path',sidecar)
        with patch.object(Path,'write_text',side_effect=OSError('private absolute path')), patch('src.literature.conversion.resources.limit_memory'), patch('logging.disable'):
            # Broken input plus disk failure must not escape as an unsanitized child traceback.
            process_file(str(source),str(target),'broken.docx','auto')


class ConversionApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name})
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.env.stop(); self.tmp.cleanup()

    def batch(self, files, **options):
        response = self.client.post('/conversion-batches',json={'root_name':'Research','files':[{'path':name,'size':len(data)} for name,data in files],**options})
        self.assertEqual(response.status_code,201,response.text)
        batch = response.json()
        for item, (_, data) in zip(batch['files'], files):
            if item['state'] == 'waiting':
                response = self.client.post(f'/conversion-batches/{batch["id"]}/files/{item["id"]}', files={'file':('browser.bin',data,'application/octet-stream')})
                self.assertEqual(response.status_code,200,response.text)
        return batch

    def finish(self,batch):
        if batch.get('kind') != 'preprocess':
            self.client.post(f'/conversion-batches/{batch["id"]}/start')
        deadline = time.monotonic()+65
        while time.monotonic() < deadline:
            value = self.client.get(f'/conversion-batches/{batch["id"]}').json()
            if value['state'] in store.TERMINAL: return value
            time.sleep(.1)
        self.fail('batch did not reach terminal state')

    def test_prepare_converts_then_classifies_and_reuses(self):
        from src.agents.preprocesser import Classification
        root = Path(self.tmp.name) / 'documents' / 'originals'
        root.mkdir(parents=True, exist_ok=True)
        (root / 'notes.txt').write_text('A research document.', encoding='utf-8')
        result = Classification(domains=['物理'], tags=['测试'], description='研究材料')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'test-model', 'OPENAI_BASE_URL': 'http://localhost:1234/v1'}), patch('src.agents.preprocesser.classify_markdown', return_value=result) as classify:
            self.assertNotEqual(self.client.post('/conversion-batches/prepare', content='{}', headers={'Content-Type': 'text/plain'}).status_code, 201)
            response = self.client.post('/conversion-batches/prepare', json={})
            self.assertEqual(response.status_code, 201, response.text)
            batch = self.finish(response.json())
            self.assertEqual(batch['state'], 'completed')
            self.assertEqual([i['format'] for i in batch['files']], ['txt', 'md'])
            self.assertEqual(batch['files'][1]['classification']['domains'], ['物理'])
            self.assertEqual(self.client.get(f"/conversion-batches/{batch['id']}/files/{batch['files'][0]['id']}/markdown").status_code, 200)
            again = self.finish(self.client.post('/conversion-batches/prepare', json={}).json())
            self.assertEqual(again['files'][-1]['code'], 'already_classified')
            self.assertEqual(classify.call_count, 1)

    def test_prepare_pdf_reuse_links_project_and_collection(self):
        from src.agents.preprocesser import Classification
        from src.literature.markdown_corpus import select_domains, read_markdown
        from src.tools.registry import ToolContext
        a = self.client.post('/projects', json={'name': 'A'}).json()['id']
        b = self.client.post('/projects', json={'name': 'B'}).json()['id']
        c = self.client.post('/projects', json={'name': 'C'}).json()['id']
        root = Path(self.tmp.name) / 'documents' / 'originals'
        root.mkdir(parents=True, exist_ok=True)
        (root / 'paper.pdf').write_bytes(pdf_fixture())
        result = Classification(domains=['物理'], tags=['测试'], description='研究材料')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'test-model', 'OPENAI_BASE_URL': 'http://localhost:1234/v1'}), patch('src.agents.preprocesser.classify_markdown', return_value=result) as classify:
            first = self.finish(self.client.post('/conversion-batches/prepare', json={'project_id': a}).json())
            paper_id = first['files'][0]['paper_id']
            self.assertIsNotNone(paper_id)
            second = self.finish(self.client.post('/conversion-batches/prepare', json={'project_id': b}).json())
            self.assertEqual(second['files'][0]['paper_id'], paper_id)
            self.assertEqual(second['files'][-1]['code'], 'already_classified')
            self.assertEqual(classify.call_count, 1)
            docs = select_domains(ToolContext(b, 'collection', (paper_id,)), ['物理'], 0)['documents']
            self.assertEqual(len(docs), 1)
            fragments = read_markdown(ToolContext(b, 'collection', (paper_id,)), docs[0]['document_id'], 'Traceable', 0)
            self.assertTrue(fragments)
            self.assertEqual(fragments[0]['paper_id'], paper_id)
            self.assertEqual(select_domains(ToolContext(c, 'local'), ['物理'], 0)['documents'], [])
            self.assertEqual(select_domains(ToolContext(b, 'collection', (paper_id + 1000,)), ['物理'], 0)['documents'], [])

    def test_preprocess_incremental_and_retry(self):
        from src.agents.preprocesser import Classification
        from src.persistence.document_classifications import get_classification
        root = Path(self.tmp.name) / 'documents' / 'conversions'
        (root / 'nested').mkdir()
        doc = root / 'nested' / 'paper.md'
        doc.write_text('original markdown', encoding='utf-8')
        result = Classification(domains=['物理', '材料'], tags=['测试'], description='研究材料')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'test-model', 'OPENAI_BASE_URL': 'http://localhost:1234/v1'}), patch('src.agents.preprocesser.classify_markdown', return_value=result) as classify:
            response = self.client.post('/conversion-batches/preprocess', json={})
            self.assertEqual(response.status_code, 201, response.text)
            batch = self.finish(response.json())
            self.assertEqual(batch['kind'], 'preprocess')
            self.assertEqual(batch['files'][0]['classification']['domains'], ['物理', '材料'])
            self.assertEqual(get_classification('nested/paper.md')['description'], '研究材料')
            again = self.finish(self.client.post('/conversion-batches/preprocess', json={}).json())
            self.assertEqual(again['counts']['skipped'], 1)
            self.assertEqual(classify.call_count, 1)
            doc.write_text('changed', encoding='utf-8')
            classify.side_effect = RuntimeError('secret upstream content')
            failed = self.finish(self.client.post('/conversion-batches/preprocess', json={}).json())
            self.assertEqual(failed['counts']['failed'], 1)
            self.assertNotIn('secret', str(failed))
            classify.side_effect = None
            retried = self.finish(self.client.post('/conversion-batches/preprocess', json={}).json())
            self.assertEqual(retried['counts']['succeeded'], 1)
            self.assertEqual(doc.read_text(encoding='utf-8'), 'changed')

    def test_preprocess_requires_json_and_nonempty_document(self):
        root = Path(self.tmp.name) / 'documents' / 'conversions'
        (root / 'empty.md').write_text('\ufeff  \n', encoding='utf-8')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'test-model', 'OPENAI_BASE_URL': 'http://localhost:1234/v1'}), patch('src.agents.preprocesser.classify_markdown') as classify:
            self.assertNotEqual(self.client.post('/conversion-batches/preprocess', content='{}', headers={'Content-Type': 'text/plain'}).status_code, 201)
            self.assertNotEqual(self.client.post('/conversion-batches/preprocess', content='{}').status_code, 201)
            self.assertFalse(store.active())
            batch = self.finish(self.client.post('/conversion-batches/preprocess', json={}).json())
            self.assertEqual(batch['counts']['failed'], 1)
            classify.assert_not_called()

    def test_preprocess_reuses_duplicate_content_across_paths(self):
        from src.agents.preprocesser import Classification
        from src.persistence.document_classifications import get_classification
        root = Path(self.tmp.name) / 'documents' / 'conversions'
        (root / 'a.md').write_text('same document', encoding='utf-8')
        (root / 'b.md').write_text('same document', encoding='utf-8')
        result = Classification(domains=['物理'], tags=['测试'], description='研究')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'test-model', 'OPENAI_BASE_URL': 'http://localhost:1234/v1'}), patch('src.agents.preprocesser.classify_markdown', return_value=result) as classify:
            batch = self.finish(self.client.post('/conversion-batches/preprocess', json={}).json())
            self.assertEqual(batch['counts']['succeeded'], 1)
            self.assertEqual(batch['counts']['skipped'], 1)
            self.assertEqual(classify.call_count, 1)
            self.assertEqual(get_classification('a.md')['domains'], ['物理'])
            self.assertEqual(get_classification('b.md')['domains'], ['物理'])
            (root / 'c.md').write_text('same document', encoding='utf-8')
            again = self.finish(self.client.post('/conversion-batches/preprocess', json={}).json())
            self.assertEqual(again['counts']['skipped'], 3)
            self.assertEqual(classify.call_count, 1)
            self.assertEqual(get_classification('c.md')['tags'], ['测试'])

    def test_preprocess_prioritizes_new_after_many_indexed(self):
        from src.agents.preprocesser import Classification
        from src.persistence.document_classifications import upsert_classification
        root = Path(self.tmp.name) / 'documents' / 'conversions'
        result = Classification(domains=['物理'], tags=['测试'], description='研究')
        for i in range(101):
            name = f'{i:03}.md'
            (root / name).write_text('old', encoding='utf-8')
            upsert_classification(name, sha256(b'old').hexdigest(), result.model_dump())
        (root / 'new.md').write_text('new', encoding='utf-8')
        with patch.dict(os.environ, {'OPENAI_MODEL': 'test-model', 'OPENAI_BASE_URL': 'http://localhost:1234/v1'}), patch('src.agents.preprocesser.classify_markdown', return_value=result) as classify:
            response = self.client.post('/conversion-batches/preprocess', json={})
            self.assertEqual(response.status_code, 201, response.text)
            batch = self.finish(response.json())
            self.assertEqual(batch['total'], 100)
            self.assertEqual(batch['counts']['succeeded'], 1)
            self.assertEqual(classify.call_count, 1)
            self.assertTrue(any(item['path'] == 'new.md' and item['state'] == 'succeeded' for item in batch['files']))
            self.assertTrue(any(item.get('classification') for item in batch['files'] if item['state'] == 'skipped'))
            self.assertEqual(self.client.post(f"/conversion-batches/{batch['id']}/start").status_code, 409)
            self.assertEqual(self.client.get(f"/conversion-batches/{batch['id']}/zip").status_code, 409)

    def test_local_originals_conversion_and_existing_markdown(self):
        root = Path(self.tmp.name) / 'documents'
        (root / 'originals' / 'nested').mkdir(parents=True)
        (root / 'originals' / 'nested' / 'paper.txt').write_text('hello', encoding='utf-8')
        response = self.client.post('/conversion-batches/from-originals', json={})
        self.assertEqual(response.status_code, 201, response.text)
        value = self.finish(response.json())
        self.assertEqual(value['counts']['succeeded'], 1)
        result = root / 'conversions' / 'nested' / 'paper.txt.md'
        self.assertIn('hello', result.read_text(encoding='utf-8'))
        response = self.client.post('/conversion-batches/from-originals', json={})
        self.assertEqual(response.status_code, 201, response.text)
        value = self.finish(response.json())
        self.assertEqual(value['counts']['skipped'], 1)
        self.assertEqual(value['files'][0]['code'], 'already_converted')
        self.assertIn('hello', result.read_text(encoding='utf-8'))

    def test_many_existing_outputs_do_not_block_new_work(self):
        root = Path(self.tmp.name) / 'documents'
        originals = root / 'originals'
        conversions = root / 'conversions'
        originals.mkdir(parents=True)
        conversions.mkdir(parents=True, exist_ok=True)
        for n in range(105):
            (originals / f'old{n:03}.txt').write_text('old', encoding='utf-8')
            (conversions / f'old{n:03}.txt.md').write_text('done', encoding='utf-8')
        (originals / 'new.txt').write_text('new content', encoding='utf-8')
        response = self.client.post('/conversion-batches/from-originals', json={})
        self.assertEqual(response.status_code, 201, response.text)
        value = self.finish(response.json())
        self.assertEqual(value['counts']['succeeded'], 1)
        self.assertEqual(value['counts']['skipped'], 99)
        self.assertIn('new content', (conversions / 'new.txt.md').read_text(encoding='utf-8'))

    def test_mixed_batch_download_zip_and_source_protection(self):
        batch = self.batch([('Paper.txt',b'Plain source'),('Paper.docx',docx_fixture()),('Deep/Study.pdf',pdf_fixture()),('bad.pdf',b'%PDF- broken'),('old.doc',b'legacy')])
        value = self.finish(batch)
        self.assertEqual(value['state'],'completed')
        self.assertEqual(value['counts']['succeeded'],3)
        self.assertEqual(value['counts']['failed'],1)
        self.assertEqual(value['counts']['skipped'],1)
        self.assertEqual(value['done'],5)
        item = value['files'][0]
        url = f'/conversion-batches/{value["id"]}/files/{item["id"]}'
        self.assertIn('Plain source',self.client.get(url+'/preview').json()['markdown'])
        self.assertIn('Plain source',self.client.get(url+'/markdown').text)
        response = self.client.get(f'/conversion-batches/{value["id"]}/zip')
        self.assertEqual(response.status_code,200)
        with ZipFile(io.BytesIO(response.content)) as zipped:
            self.assertIn('Research-Markdown/Deep/Study.md',zipped.namelist())
            self.assertIn('Research-Markdown/Paper.txt.md',zipped.namelist())
            self.assertIn('Research-Markdown/Paper.docx.md',zipped.namelist())
            self.assertNotIn('bad.md',zipped.namelist())
        directory = Path(self.tmp.name)/'documents'/'conversions'/'.batches'/value['id']
        self.assertFalse(list(directory.glob('*.input')))
        self.assertFalse(list(directory.glob('*.zip')))
        self.assertNotIn(str(Path(self.tmp.name)),json.dumps(value))

    def test_path_traversal_and_upload_limits(self):
        for path in ('../x.txt','/x.txt','C:/x.txt','a\\x.txt','a/../b.txt','CON.txt','a/x.','a//x.txt','hidden\u202e.txt'):
            response = self.client.post('/conversion-batches',json={'root_name':'safe','files':[{'path':path,'size':4}]})
            self.assertEqual(response.status_code,422,path)
        response = self.client.post('/conversion-batches',json={'root_name':'safe','files':[{'path':'x.txt','size':MAX_FILE+1}]})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['files'][0]['code'],'file_limit')
        response = self.client.post('/conversion-batches', content=b'x',headers={'Content-Length':str(MAX_FILE+100000)})
        self.assertEqual(response.status_code,413)

    def test_missing_upload_duplicate_submit_and_cancel(self):
        response = self.client.post('/conversion-batches',json={'root_name':'safe','files':[{'path':'missing.txt','size':4}]})
        batch = response.json()
        result = self.finish(batch)
        self.assertEqual(result['files'][0]['code'],'upload_missing')
        self.assertEqual(self.client.post(f'/conversion-batches/{batch["id"]}/start').json()['state'],'completed')
        other = self.batch([('a.txt',b'cancel me')])
        cancelled = self.client.post(f'/conversion-batches/{other["id"]}/cancel').json()
        self.assertEqual(cancelled['state'],'cancelled')
        self.assertEqual(self.client.post(f'/conversion-batches/{other["id"]}/start').json()['state'],'cancelled')

    def test_truncated_upload_does_not_block_other_files(self):
        batch = self.client.post('/conversion-batches',json={'root_name':'safe','files':[{'path':'short.txt','size':10},{'path':'good.txt','size':4}]}).json()
        url = f'/conversion-batches/{batch["id"]}'
        response = self.client.post(url+'/files/'+batch['files'][0]['id'],files={'file':('short',b'short')})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.post(url+'/files/'+batch['files'][1]['id'],files={'file':('good',b'good')}).status_code,200)
        value = self.finish(batch)
        self.assertEqual(value['counts']['failed'],1)
        self.assertEqual(value['counts']['succeeded'],1)
        directory = Path(self.tmp.name)/'documents'/'conversions'/'.batches'/batch['id']
        self.assertFalse(list(directory.glob('*.upload')))
        self.assertFalse(list(directory.glob('*.input')))

    def test_existing_pdf_import_preserves_old_page_text_and_version(self):
        project = self.client.post('/projects',json={'name':'Existing PDF'}).json()['id']
        data = pdf_fixture()
        response = self.client.post(f'/projects/{project}/papers/import',files={'file':('old.pdf',data,'application/pdf')})
        self.assertEqual(response.status_code,201,response.text)
        from src.literature.evidence import collect_evidence
        records = repo.local_search(project,'Traceable')
        before = collect_evidence(project,'local',None,'Traceable',records)
        value = self.finish(self.batch([('new-name.pdf',data)],project_id=project))
        self.assertEqual(value['files'][0]['paper_id'],response.json()['id'])
        after = collect_evidence(project,'local',None,'Traceable',repo.local_search(project,'Traceable'))
        self.assertEqual(before,after)
        with connect_database() as db: self.assertEqual(db.execute('SELECT count(*) FROM paper_versions').fetchone()[0],1)

    def test_timeout_terminates_child_and_next_file_continues(self):
        app.state.conversions.timeout = .001
        value = self.finish(self.batch([('slow.txt',b'synthetic source'),('next.txt',b'next source')]))
        self.assertEqual(value['counts']['failed'],2)
        self.assertEqual({f['code'] for f in value['files']},{'conversion_timeout'})

    def test_restart_finalizes_unfinished_batch(self):
        batch = self.batch([('a.txt',b'pending document')])
        self.client.__exit__(None,None,None)
        self.client.__enter__()
        value = self.client.get(f'/conversion-batches/{batch["id"]}').json()
        self.assertEqual(value['state'],'interrupted')
        self.assertEqual(value['files'][0]['code'],'service_interrupted')

    def test_cancel_running_child_and_keep_api_available(self):
        batch = self.batch([('slow.txt',b'long operation')])
        with patch('apps.api.conversion_runner.process_file',slow_conversion):
            self.client.post(f'/conversion-batches/{batch["id"]}/start')
            deadline = time.monotonic()+5
            while time.monotonic()<deadline:
                value = self.client.get(f'/conversion-batches/{batch["id"]}').json()
                if value['files'][0]['state']=='running': break
                time.sleep(.05)
            self.assertEqual(value['files'][0]['state'],'running')
            started = time.monotonic()
            self.assertEqual(self.client.get('/health').status_code,200)
            self.assertLess(time.monotonic()-started,1)
            value = self.client.post(f'/conversion-batches/{batch["id"]}/cancel').json()
            self.assertEqual(value['files'][0]['state'],'cancelled')
            directory = Path(self.tmp.name)/'documents'/'conversions'/'.batches'/batch['id']
            deadline = time.monotonic()+5
            while list(directory.glob('*.input')) and time.monotonic()<deadline: time.sleep(.05)
            self.assertFalse(list(directory.glob('*.input')))
        self.assertEqual(self.finish(self.batch([('next.txt',b'next operation')]))['counts']['succeeded'],1)

    def test_batch_limit_stable_names_and_100_file_progress(self):
        names = ['a.pdf','a.txt','a.pdf.docx']
        def manifest(paths):
            response = self.client.post('/conversion-batches',json={'root_name':'safe','files':[{'path':p,'size':0} for p in paths]})
            self.assertEqual(response.status_code,201,response.text)
            value = response.json()
            self.client.post(f'/conversion-batches/{value["id"]}/cancel')
            return {f['path']:f['output'] for f in value['files']}
        first = manifest(names)
        self.assertEqual(first,manifest(list(reversed(names))))
        self.assertEqual(len(set(first.values())),3)
        response = self.client.post('/conversion-batches',json={'root_name':'safe','files':[{'path':f'{i}.txt','size':1} for i in range(101)]})
        self.assertEqual(response.status_code,422)
        started = time.monotonic()
        value = self.finish(self.batch([(f'dir/{i}.txt',b'fixed bulk source') for i in range(100)]))
        self.assertEqual(value['done'],100)
        self.assertEqual(value['counts']['succeeded'],100)
        self.assertEqual(len({f['id'] for f in value['files']}),100)
        self.assertTrue(all(f['elapsed']>0 for f in value['files']))
        self.assertLess(time.monotonic()-started,65)

    def test_opt_in_pdf_import_scope_dedupe_pages_and_evidence(self):
        a = self.client.post('/projects',json={'name':'A'}).json()['id']
        b = self.client.post('/projects',json={'name':'B'}).json()['id']
        data = pdf_fixture()
        value = self.finish(self.batch([('paper.pdf',data),('note.txt',b'not a paper')],project_id=a))
        self.assertEqual(value['counts']['succeeded'],2)
        self.assertIsNotNone(value['files'][0]['paper_id'])
        self.assertIsNone(value['files'][1]['paper_id'])
        papers = repo.local_search(a,'Traceable')
        self.assertEqual(len(papers),1)
        self.assertEqual(repo.local_search(b,'Traceable'),[])
        from src.literature.evidence import collect_evidence
        evidence = collect_evidence(a, 'local', (), 'Traceable', papers)
        self.assertEqual({item['page'] for item in evidence},{1,2})
        self.assertTrue(all(item['paper_id']==papers[0]['id'] for item in evidence))
        restricted = collect_evidence(b, 'local', (), 'Traceable', papers)
        self.assertEqual(restricted,[])
        paper_id = papers[0]['id']
        with connect_database() as db:
            before = db.execute('SELECT * FROM paper_pages ORDER BY page').fetchall()
            self.assertEqual([r[1] for r in before],[1,2])
        value = self.finish(self.batch([('renamed.pdf',data)],project_id=a))
        self.assertEqual(value['files'][0]['paper_id'],paper_id)
        with connect_database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM paper_versions').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT * FROM paper_pages ORDER BY page').fetchall(),before)
        original = Path(self.tmp.name)/'documents'/'originals'/'.store'/f'{sha256(data).hexdigest()}.pdf'
        self.assertEqual(original.read_bytes(),data)


if __name__ == '__main__': unittest.main()
