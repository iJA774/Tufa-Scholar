"""Small shared repository for the API and single worker."""
import json
import logging
import re
from src.persistence.storage import connect_database


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
        db.execute('DELETE FROM collection_papers WHERE collection_id IN (SELECT id FROM collections WHERE project_id=?)', (project_id,))
        db.execute('DELETE FROM collections WHERE project_id=?', (project_id,))
        db.execute('DELETE FROM reports WHERE project_id=?', (project_id,))
        db.execute('DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE project_id=?)', (project_id,))
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
        conversations = _all(db, 'SELECT * FROM conversations WHERE project_id=? ORDER BY id', (project_id,))
        for conversation in conversations:
            conversation['pinned'] = bool(conversation['pinned'])
        return conversations


def list_all_conversations():
    with connect_database() as db:
        conversations = _all(db, """
            SELECT c.id, c.project_id, c.title, c.pinned, MAX(m.created_at) AS updated_at
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id=c.id
            GROUP BY c.id
            ORDER BY c.pinned DESC, MAX(m.created_at) DESC, MAX(m.id) DESC, c.id DESC
        """)
        for conversation in conversations:
            conversation['pinned'] = bool(conversation['pinned'])
        return conversations


def create_conversation(project_id, title):
    with connect_database() as db:
        cur = db.execute('INSERT INTO conversations(project_id,title) VALUES (?,?)', (project_id,title))
        db.commit()
        return {'id': cur.lastrowid, 'project_id': project_id, 'title': title, 'pinned': False}


def update_conversation_pinned(conversation_id, pinned):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE conversations SET pinned=? WHERE id=?', (int(pinned), conversation_id))
        conversation = _one(db, """
            SELECT c.id, c.project_id, c.title, c.pinned, MAX(m.created_at) AS updated_at
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id=c.id
            WHERE c.id=?
            GROUP BY c.id
        """, (conversation_id,))
        db.commit()
        if conversation:
            conversation['pinned'] = bool(conversation['pinned'])
        return conversation


def delete_conversation(conversation_id):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not _one(db, 'SELECT id FROM conversations WHERE id=?', (conversation_id,)):
            return 'missing'
        if _one(db, "SELECT id FROM tasks WHERE conversation_id=? AND status IN ('pending','running') LIMIT 1", (conversation_id,)):
            return 'active'

        # Retries point to an earlier task in the same conversation. Clear these
        # nullable self-references before removing the conversation's task rows.
        db.execute('UPDATE tasks SET origin_run_id=NULL WHERE conversation_id=?', (conversation_id,))
        db.execute('DELETE FROM reports WHERE conversation_id=?', (conversation_id,))
        db.execute('DELETE FROM messages WHERE conversation_id=?', (conversation_id,))
        db.execute('DELETE FROM tasks WHERE conversation_id=?', (conversation_id,))
        db.execute('DELETE FROM conversations WHERE id=?', (conversation_id,))
        db.commit()
        return 'deleted'


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


def save_paper(project_id, metadata, version=None, collection_id=None):
    doi = _normalize_doi(metadata.get('doi'))
    arxiv = _normalize_arxiv(metadata.get('arxiv_id'))
    with connect_database() as db:
        if collection_id is not None:
            db.execute('BEGIN IMMEDIATE')
            if not _one(db, 'SELECT id FROM collections WHERE id=? AND project_id=?', (collection_id,project_id)):
                return None
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
        if collection_id is not None:
            db.execute('INSERT OR IGNORE INTO collection_papers VALUES (?,?)', (collection_id,paper_id))
        if version and not existing_version(db, version['digest']):
            cur = db.execute('INSERT INTO paper_versions(paper_id,filename,digest,path,text,text_status,pages_indexed) VALUES (?,?,?,?,?,?,?)', (paper_id,version['filename'],version['digest'],version['path'],version['text'],version['text_status'],int('pages' in version)))
            if 'pages' in version:
                db.executemany('INSERT INTO paper_pages(version_id,page,text) VALUES (?,?,?)', ((cur.lastrowid, n, text) for n, text in enumerate(version['pages'], 1)))
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


def local_search(project_id, query, paper_ids=None):
    if paper_ids is not None and not paper_ids:
        return []
    terms = re.findall(r'\w+', query, flags=re.UNICODE)
    if not terms:
        return []
    match = ' OR '.join('"' + term + '"' for term in terms)
    with connect_database() as db:
        scope = '' if paper_ids is None else ' AND p.id IN (' + ','.join('?' for _ in paper_ids) + ')'
        rows = _all(db, 'SELECT p.* FROM paper_fts JOIN papers p ON p.id=paper_fts.rowid JOIN project_papers pp ON pp.paper_id=p.id WHERE pp.project_id=? AND paper_fts MATCH ?' + scope + ' ORDER BY bm25(paper_fts), p.id LIMIT 20', (project_id, match, *(paper_ids or [])))
        return [_paper(db, row) for row in rows]


def _task(row):
    if row and row['result'] is not None:
        row['result'] = json.loads(row['result'])
    if row:
        row['subtasks'] = json.loads(row.get('subtasks') or '[]')
    if row and row.get('paper_ids') is not None:
        row['paper_ids'] = json.loads(row['paper_ids'])
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

    with connect_database() as db:
        cur = db.execute("INSERT INTO tasks(project_id,conversation_id,query,source,status,stage,kind,origin_run_id,collection_id,paper_ids,research_mode) VALUES (?,?,?,?,'pending','pending',?,?,?,?,?)", (task['project_id'],task['conversation_id'],task['query'],task['source'],task['kind'],task.get('origin_run_id') or task_id,task.get('collection_id'),json.dumps(task['paper_ids']) if task.get('paper_ids') is not None else None,task.get('research_mode') or 'auto'))
        db.execute('UPDATE tasks SET model_name=?,reasoning_effort=? WHERE id=?', (task.get('model_name'),task.get('reasoning_effort'),cur.lastrowid))
        db.commit()
        return get_task(cur.lastrowid)


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


def create_question(project_id, conversation_id, question, source, collection_id=None, model_name=None, reasoning_effort=None, research_mode='auto'):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not _one(db, 'SELECT id FROM projects WHERE id=?', (project_id,)):
            return None
        paper_ids = None
        if source == 'collection':
            if not _one(db, 'SELECT id FROM collections WHERE id=? AND project_id=?', (collection_id,project_id)):
                return None
            paper_ids = [r['paper_id'] for r in _all(db, 'SELECT paper_id FROM collection_papers WHERE collection_id=? ORDER BY paper_id', (collection_id,))]
        if conversation_id is None:
            conversation_id = db.execute('INSERT INTO conversations(project_id,title) VALUES (?,?)', (project_id,question[:80])).lastrowid
        elif not _one(db, 'SELECT id FROM conversations WHERE id=? AND project_id=?', (conversation_id,project_id)):
            return None
        run_id = db.execute("INSERT INTO tasks(project_id,conversation_id,query,source,status,stage,kind,collection_id,paper_ids,research_mode) VALUES (?,?,?,?, 'pending','pending','chat',?,?,?)", (project_id,conversation_id,question,source,collection_id,json.dumps(paper_ids) if paper_ids is not None else None,research_mode)).lastrowid
        db.execute('UPDATE tasks SET model_name=?,reasoning_effort=? WHERE id=?', (model_name, reasoning_effort, run_id))
        db.execute("INSERT INTO messages(conversation_id,role,content,run_id) VALUES (?,'user',?,?)", (conversation_id,question,run_id))
        db.commit()
        return {'run_id':run_id,'conversation_id':conversation_id}


def list_messages(conversation_id):
    with connect_database() as db:
        if not _one(db, 'SELECT id FROM conversations WHERE id=?', (conversation_id,)):
            return None
        rows = _all(db, 'SELECT * FROM messages WHERE conversation_id=? ORDER BY id', (conversation_id,))
        for row in rows:
            row['metadata'] = json.loads(row['metadata']) if row['metadata'] else None
        return rows


def record_subtask(task_id, note):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = _one(db, "SELECT subtasks FROM tasks WHERE id=? AND kind='chat' AND status='running'", (task_id,))
        if row:
            notes = json.loads(row['subtasks'] or '[]')
            if not any(item.get('index') == note['index'] for item in notes):
                notes.append(note)
                notes.sort(key=lambda item: item['index'])
                db.execute('UPDATE tasks SET subtasks=? WHERE id=?', (json.dumps(notes, ensure_ascii=False), task_id))
        db.commit()


def update_stage(task_id, stage):
    logging.getLogger('tufa.worker').info('task_stage run_id=%s stage=%s', task_id, stage)
    with connect_database() as db:
        db.execute("UPDATE tasks SET stage=? WHERE id=? AND status='running'", (stage,task_id)); db.commit()


def task_history(task_id):
    with connect_database() as db:
        task = _one(db, 'SELECT * FROM tasks WHERE id=?', (task_id,))
        rows = _all(db, 'SELECT role,content FROM messages WHERE conversation_id=? AND run_id<=? ORDER BY id DESC LIMIT 40', (task['conversation_id'], task.get('origin_run_id') or task_id))
        return list(reversed(rows))


def finish_chat_task(task_id, content, evidence):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        task = _one(db, "SELECT * FROM tasks WHERE id=? AND kind='chat' AND status='running'", (task_id,))
        if not task:
            raise ValueError('任务不在运行中')
        if task['source'] == 'collection':
            allowed = set(json.loads(task['paper_ids']))
            if any(e.get('paper_id') not in allowed for e in evidence):
                raise ValueError('证据不属于所选收藏夹')
        encoded = json.dumps(evidence, ensure_ascii=False)
        report_id = db.execute('INSERT INTO reports(project_id,conversation_id,run_id,content,evidence) VALUES (?,?,?,?,?)', (task['project_id'],task['conversation_id'],task_id,content,encoded)).lastrowid
        metadata = {'report_id':report_id,'evidence':evidence}
        db.execute("INSERT INTO messages(conversation_id,role,content,run_id,metadata) VALUES (?,'assistant',?,?,?)", (task['conversation_id'],content,task_id,json.dumps(metadata,ensure_ascii=False)))
        result = {'report_id':report_id,'content':content,'evidence':evidence}
        db.execute("UPDATE tasks SET status='succeeded',stage='completed',result=?,error=NULL WHERE id=?", (json.dumps(result,ensure_ascii=False),task_id))
        db.commit()
        return result


def get_report(report_id):
    with connect_database() as db:
        report = _one(db, 'SELECT * FROM reports WHERE id=?', (report_id,))
        if report: report['evidence'] = json.loads(report['evidence'])
        return report


def project_evidence(project_id, paper_ids=None):
    with connect_database() as db:
        papers = _all(db, 'SELECT p.* FROM papers p JOIN project_papers pp ON pp.paper_id=p.id WHERE pp.project_id=?', (project_id,))
        if paper_ids is not None:
            papers = [p for p in papers if p['id'] in set(paper_ids)]
        for p in papers:
            p['authors'] = json.loads(p['authors'])
            p['versions'] = _all(db, 'SELECT id, paper_id, path, filename, text_status, pages_indexed FROM paper_versions WHERE paper_id=? ORDER BY id DESC', (p['id'],))
        return papers


def search_project(project_id, query):
    pattern = '%' + query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    with connect_database() as db:
        conversations = _all(db, "SELECT DISTINCT c.* FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id WHERE c.project_id=? AND (c.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\') ORDER BY c.id DESC", (project_id,pattern,pattern))
        papers = _all(db, "SELECT DISTINCT p.* FROM papers p JOIN project_papers pp ON pp.paper_id=p.id LEFT JOIN paper_fts f ON f.rowid=p.id WHERE pp.project_id=? AND (p.title LIKE ? ESCAPE '\\' OR p.abstract LIKE ? ESCAPE '\\' OR f.full_text LIKE ? ESCAPE '\\') ORDER BY p.id DESC LIMIT 30", (project_id,pattern,pattern,pattern))
        return {'conversations':conversations[:30],'papers':[_paper(db,p) for p in papers]}


def collections(project_id):
    with connect_database() as db:
        return _all(db, 'SELECT * FROM collections WHERE project_id=? ORDER BY id', (project_id,))


def create_collection(project_id, name):
    with connect_database() as db:
        cur = db.execute('INSERT INTO collections(project_id,name) VALUES (?,?)', (project_id,name)); db.commit()
        return {'id':cur.lastrowid,'project_id':project_id,'name':name}


def delete_collection(project_id, collection_id):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not _one(db, 'SELECT id FROM collections WHERE id=? AND project_id=?', (collection_id,project_id)):
            return False
        db.execute('DELETE FROM collection_papers WHERE collection_id=?', (collection_id,))
        db.execute('DELETE FROM collections WHERE id=?', (collection_id,))
        db.commit()
        return True


def collection_papers(project_id, collection_id):
    with connect_database() as db:
        if not _one(db, 'SELECT id FROM collections WHERE id=? AND project_id=?', (collection_id,project_id)): return None
        return [_paper(db,p) for p in _all(db, 'SELECT p.* FROM papers p JOIN collection_papers cp ON cp.paper_id=p.id WHERE cp.collection_id=? ORDER BY p.id', (collection_id,))]


def collection_add_existing(project_id, collection_id, paper_id):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not _one(db, 'SELECT id FROM collections WHERE id=? AND project_id=?', (collection_id,project_id)) or not _one(db,'SELECT paper_id FROM project_papers WHERE project_id=? AND paper_id=?',(project_id,paper_id)): return None
        db.execute('INSERT OR IGNORE INTO collection_papers VALUES (?,?)',(collection_id,paper_id)); db.commit()
        return _paper(db,_one(db,'SELECT * FROM papers WHERE id=?',(paper_id,)))


def collection_remove(project_id, collection_id, paper_id):
    with connect_database() as db:
        cur=db.execute('DELETE FROM collection_papers WHERE collection_id IN (SELECT id FROM collections WHERE id=? AND project_id=?) AND paper_id=?',(collection_id,project_id,paper_id)); db.commit(); return bool(cur.rowcount)


def version_pages(version_id):
    with connect_database() as db:
        return _all(db, 'SELECT page,text FROM paper_pages WHERE version_id=? ORDER BY page', (version_id,))


def index_version_pages(version_id, pages):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = _one(db, 'SELECT pages_indexed FROM paper_versions WHERE id=?', (version_id,))
        if row and not row['pages_indexed']:
            db.executemany('INSERT INTO paper_pages(version_id,page,text) VALUES (?,?,?)', ((version_id, n, text) for n, text in enumerate(pages, 1)))
            db.execute('UPDATE paper_versions SET pages_indexed=1 WHERE id=?', (version_id,))
        db.commit()
