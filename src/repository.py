"""Small shared repository for the API and single worker."""
import json
import re
from src.storage import connect_database


def _dict(row, cursor):
    return dict(zip((c[0] for c in cursor.description), row)) if row else None


def _one(db, sql, args=()):
    cur = db.execute(sql, args)
    return _dict(cur.fetchone(), cur)


def _all(db, sql, args=()):
    cur = db.execute(sql, args)
    return [_dict(row, cur) for row in cur.fetchall()]


def _paper(db, paper):
    if paper is None:
        return None
    paper['authors'] = json.loads(paper['authors'])
    paper['open_access'] = None if paper['open_access'] is None else bool(paper['open_access'])
    paper['versions'] = _all(db, 'SELECT id, paper_id, filename, text_status FROM paper_versions WHERE paper_id=? ORDER BY id', (paper['id'],))
    for version in paper['versions']:
        version['file_url'] = f"/paper-versions/{version['id']}/file"
    paper['source_level'] = ('full_text' if any(v['text_status'] == 'text_available' for v in paper['versions']) else ('abstract' if paper['abstract'] else 'metadata_only'))
    return paper


def list_projects():
    with connect_database() as db:
        return _all(db, 'SELECT * FROM projects ORDER BY id')


def create_project(name):
    with connect_database() as db:
        cur = db.execute('INSERT INTO projects(name) VALUES (?)', (name,))
        db.commit()
        return {'id': cur.lastrowid, 'name': name}


def delete_project(project_id):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not _one(db, 'SELECT id FROM projects WHERE id=?', (project_id,)):
            return 'missing'
        if _one(db, "SELECT id FROM tasks WHERE project_id=? AND status='running' LIMIT 1", (project_id,)):
            return 'running'
        db.execute('DELETE FROM tasks WHERE project_id=?', (project_id,))
        db.execute('DELETE FROM conversations WHERE project_id=?', (project_id,))
        db.execute('DELETE FROM project_papers WHERE project_id=?', (project_id,))
        db.execute('DELETE FROM projects WHERE id=?', (project_id,))
        db.commit()
        return 'deleted'


def project_exists(project_id):
    with connect_database() as db:
        return bool(_one(db, 'SELECT id FROM projects WHERE id=?', (project_id,)))


def list_conversations(project_id):
    with connect_database() as db:
        return _all(db, 'SELECT * FROM conversations WHERE project_id=? ORDER BY id', (project_id,))


def create_conversation(project_id, title):
    with connect_database() as db:
        cur = db.execute('INSERT INTO conversations(project_id,title) VALUES (?,?)', (project_id,title))
        db.commit()
        return {'id': cur.lastrowid, 'project_id': project_id, 'title': title}


def conversation_exists(project_id, conversation_id):
    with connect_database() as db:
        return bool(_one(db, 'SELECT id FROM conversations WHERE id=? AND project_id=?', (conversation_id, project_id)))


def list_papers(project_id):
    with connect_database() as db:
        return [_paper(db, p) for p in _all(db, 'SELECT p.* FROM papers p JOIN project_papers pp ON p.id=pp.paper_id WHERE pp.project_id=? ORDER BY p.id', (project_id,))]


def _normalize_doi(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value.strip(), flags=re.I).lower() if value else None


def _normalize_arxiv(value):
    return re.sub(r'v\d+$', '', re.sub(r'^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)', '', value.strip(), flags=re.I).lower().removesuffix('.pdf')) if value else None


def save_paper(project_id, metadata, version=None):
    doi = _normalize_doi(metadata.get('doi'))
    arxiv = _normalize_arxiv(metadata.get('arxiv_id'))
    with connect_database() as db:
        existing = None
        if version:
            existing = _one(db, 'SELECT p.* FROM papers p JOIN paper_versions v ON p.id=v.paper_id WHERE v.digest=?', (version['digest'],))
        by_doi = _one(db, 'SELECT * FROM papers WHERE doi=?', (doi,)) if doi else None
        by_arxiv = _one(db, 'SELECT * FROM papers WHERE arxiv_id=?', (arxiv,)) if arxiv else None
        matches = {p['id'] for p in (existing, by_doi, by_arxiv) if p}
        if len(matches) > 1:
            raise ValueError('文件、DOI 或 arXiv ID 对应不同文献')
        existing = existing or by_doi or by_arxiv
        if existing:
            paper_id = existing['id']
            # Fill absent identifiers and metadata without erasing previously saved information.
            db.execute("UPDATE papers SET title=?, doi=COALESCE(doi,?), arxiv_id=COALESCE(arxiv_id,?), abstract=COALESCE(abstract,?), year=COALESCE(year,?), authors=CASE WHEN authors='[]' AND ?!='[]' THEN ? ELSE authors END, source=COALESCE(source,?), open_access=COALESCE(open_access,?), pdf_url=COALESCE(pdf_url,?), original_url=COALESCE(original_url,?) WHERE id=?", (metadata.get('title') if not version else existing['title'], doi, arxiv, metadata.get('abstract'), metadata.get('year'), json.dumps(metadata.get('authors') or [], ensure_ascii=False), json.dumps(metadata.get('authors') or [], ensure_ascii=False), metadata.get('source'), metadata.get('open_access'), metadata.get('pdf_url'), metadata.get('original_url'), paper_id))
        else:
            cur = db.execute('INSERT INTO papers(title,doi,arxiv_id,abstract,year,authors,source,open_access,pdf_url,original_url) VALUES (?,?,?,?,?,?,?,?,?,?)', (metadata['title'],doi,arxiv,metadata.get('abstract'),metadata.get('year'),json.dumps(metadata.get('authors') or [], ensure_ascii=False),metadata.get('source'),metadata.get('open_access'),metadata.get('pdf_url'),metadata.get('original_url')))
            paper_id = cur.lastrowid
        db.execute('INSERT OR IGNORE INTO project_papers(project_id,paper_id) VALUES (?,?)', (project_id,paper_id))
        if version and not existing_version(db, version['digest']):
            db.execute('INSERT INTO paper_versions(paper_id,filename,digest,path,text,text_status) VALUES (?,?,?,?,?,?)', (paper_id,version['filename'],version['digest'],version['path'],version['text'],version['text_status']))
        row = _one(db, 'SELECT * FROM papers WHERE id=?', (paper_id,))
        full_text = ' '.join(v['text'] for v in _all(db, 'SELECT text FROM paper_versions WHERE paper_id=?', (paper_id,)))
        db.execute('DELETE FROM paper_fts WHERE rowid=?', (paper_id,))
        db.execute('INSERT INTO paper_fts(rowid,title,abstract,full_text) VALUES (?,?,?,?)', (paper_id,row['title'],row['abstract'] or '',full_text))
        db.commit()
        return _paper(db, row)


def existing_version(db, digest):
    return _one(db, 'SELECT id FROM paper_versions WHERE digest=?', (digest,))


def version_file(version_id):
    with connect_database() as db:
        return _one(db, 'SELECT path, filename FROM paper_versions WHERE id=?', (version_id,))


def local_search(project_id, query):
    terms = re.findall(r'\w+', query, flags=re.UNICODE)
    if not terms:
        return []
    match = ' OR '.join('"' + term + '"' for term in terms)
    with connect_database() as db:
        rows = _all(db, 'SELECT p.* FROM paper_fts JOIN papers p ON p.id=paper_fts.rowid JOIN project_papers pp ON pp.paper_id=p.id WHERE pp.project_id=? AND paper_fts MATCH ? ORDER BY bm25(paper_fts), p.id LIMIT 20', (project_id, match))
        return [_paper(db, row) for row in rows]


def _task(row):
    if row and row['result'] is not None:
        row['result'] = json.loads(row['result'])
    return row


def create_task(project_id, conversation_id, query, source):
    with connect_database() as db:
        cur = db.execute("INSERT INTO tasks(project_id,conversation_id,query,source,status,stage) VALUES (?,?,?,?, 'pending','pending')", (project_id,conversation_id,query,source))
        db.commit()
        return cur.lastrowid


def get_task(task_id):
    with connect_database() as db:
        return _task(_one(db, 'SELECT * FROM tasks WHERE id=?', (task_id,)))


def list_tasks(project_id, conversation_id=None):
    with connect_database() as db:
        if conversation_id is None:
            rows = _all(db, 'SELECT * FROM tasks WHERE project_id=? ORDER BY id DESC LIMIT 20', (project_id,))
        else:
            rows = _all(db, 'SELECT * FROM tasks WHERE project_id=? AND conversation_id=? ORDER BY id DESC LIMIT 20', (project_id, conversation_id))
        return [_task(row) for row in rows]


def retry_task(task_id):
    task = get_task(task_id)
    if task is None or task['status'] not in ('failed', 'interrupted'):
        return None
    return get_task(create_task(task['project_id'], task['conversation_id'], task['query'], task['source']))


def claim_task():
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        task = _one(db, "SELECT * FROM tasks WHERE status='pending' ORDER BY id LIMIT 1")
        if task:
            db.execute("UPDATE tasks SET status='running',stage='searching' WHERE id=?", (task['id'],))
            task['status'], task['stage'] = 'running', 'searching'
        db.commit()
        return _task(task)


def finish_task(task_id, result):
    with connect_database() as db:
        db.execute("UPDATE tasks SET status='succeeded',stage='completed',result=?,error=NULL WHERE id=?", (json.dumps(result, ensure_ascii=False),task_id))
        db.commit()


def fail_task(task_id, error):
    with connect_database() as db:
        db.execute("UPDATE tasks SET status='failed',stage='failed',error=? WHERE id=?", (str(error),task_id))
        db.commit()


def interrupt_running_tasks():
    with connect_database() as db:
        db.execute("UPDATE tasks SET status='interrupted',stage='interrupted',error='Worker 重启，任务已中断' WHERE status='running'")
        db.commit()
