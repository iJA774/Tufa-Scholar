"""Project-scoped, versioned Markdown corpus."""
import hashlib
import json
import re
from pathlib import Path
from .storage import connect_database


def index_document(project_id, path, digest, markdown, paper_id=None):
    path = str(path)
    document_id = hashlib.sha256((path + '\0' + digest).encode()).hexdigest()
    # The conversion preview uses universal newlines; offsets always refer to that text.
    markdown = markdown.replace('\r\n', '\n').replace('\r', '\n')
    front = re.match(r'\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)', markdown, re.S)
    body_start = front.end() if front else 0
    title = None
    if front:
        title_line = re.search(r'^title:[ \t]*(.*)$', front.group(1), re.M)
        if title_line:
            try:
                parsed = json.loads(title_line.group(1))
                if isinstance(parsed, str) and parsed.strip():
                    title = parsed.strip()
            except ValueError:
                pass
    heading = re.search(r'^#[ \t]+(.+?)[ \t]*$', markdown[body_start:], re.M)
    title = (title or (heading.group(1) if heading else Path(path).stem))[:300]
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if paper_id is not None and not db.execute('SELECT 1 FROM project_papers WHERE project_id=? AND paper_id=?', (project_id, paper_id)).fetchone():
            raise ValueError('论文不属于当前项目')
        db.execute('INSERT OR IGNORE INTO markdown_documents(id,path,digest,title) VALUES (?,?,?,?)', (document_id, path, digest, title))
        metadata = db.execute('SELECT description FROM document_classifications WHERE path=? AND digest=?', (path, digest)).fetchone()
        if metadata and not db.execute('SELECT 1 FROM markdown_metadata WHERE document_id=?', (document_id,)).fetchone():
            db.execute('INSERT INTO markdown_metadata VALUES (?,?)', (document_id, metadata[0]))
            db.execute('INSERT INTO markdown_domains SELECT ?,domain FROM document_domains WHERE path=? LIMIT 50', (document_id, path))
            db.execute('INSERT INTO markdown_tags SELECT ?,tag FROM document_tags WHERE path=? LIMIT 50', (document_id, path))
        if db.execute('SELECT index_format FROM markdown_documents WHERE id=?', (document_id,)).fetchone()[0] != 1:
            db.execute('DELETE FROM markdown_fts WHERE rowid IN (SELECT id FROM markdown_chunks WHERE document_id=?)', (document_id,))
            db.execute('DELETE FROM markdown_chunks WHERE document_id=?', (document_id,))
            db.execute('UPDATE markdown_documents SET title=?,index_format=1 WHERE id=?', (title,document_id))
            for start in range(body_start, len(markdown), 1200):
                chunk = markdown[start:start+1200]
                cursor = db.execute('INSERT INTO markdown_chunks(document_id,char_start,char_end,text) VALUES (?,?,?,?)', (document_id,start,start+len(chunk),chunk))
                db.execute('INSERT INTO markdown_fts(rowid,text) VALUES (?,?)', (cursor.lastrowid,chunk))
        db.execute('DELETE FROM project_markdown WHERE project_id=? AND document_id IN (SELECT id FROM markdown_documents WHERE path=?)', (project_id,path))
        db.execute('INSERT INTO project_markdown VALUES (?,?,?)', (project_id,document_id,paper_id))
        db.commit()
    return document_id

def is_indexed(project_id, path, digest):
    with connect_database() as db:
        return db.execute('SELECT 1 FROM project_markdown pm JOIN markdown_documents d ON d.id=pm.document_id JOIN markdown_metadata m ON m.document_id=d.id WHERE pm.project_id=? AND d.path=? AND d.digest=? AND d.index_format=1', (project_id,str(path),digest)).fetchone() is not None
