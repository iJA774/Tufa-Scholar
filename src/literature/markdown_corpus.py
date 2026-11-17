"""Scoped metadata and lexical retrieval for indexed Markdown."""
import re
from src.persistence.storage import connect_database


def _scope(context):
    if not context.local_literature or context.source not in ('local', 'all', 'collection'):
        return '0', []
    if context.source == 'collection':
        if not context.paper_ids:
            return '0', []
        return 'pm.project_id=? AND pm.paper_id IN (' + ','.join('?' for _ in context.paper_ids) + ') AND EXISTS (SELECT 1 FROM project_papers pp WHERE pp.project_id=pm.project_id AND pp.paper_id=pm.paper_id)', [context.project_id, *context.paper_ids]
    return 'pm.project_id=?', [context.project_id]


def list_local_domains(context):
    scope, args = _scope(context)
    with connect_database() as db:
        return [r[0] for r in db.execute('SELECT DISTINCT dom.domain FROM project_markdown pm JOIN markdown_documents d ON d.id=pm.document_id JOIN markdown_metadata c ON c.document_id=d.id JOIN markdown_domains dom ON dom.document_id=d.id WHERE '+scope+' ORDER BY dom.domain LIMIT 500', args)]


def select_domains(context, domains, offset):
    scope, args = _scope(context)
    if not domains:
        return {'documents': [], 'next_offset': None}
    sql = 'SELECT DISTINCT d.id,d.title,d.id,c.description FROM project_markdown pm JOIN markdown_documents d ON d.id=pm.document_id JOIN markdown_metadata c ON c.document_id=d.id JOIN markdown_domains dom ON dom.document_id=d.id WHERE '+scope+' AND dom.domain IN ('+','.join('?' for _ in domains)+') ORDER BY d.title,d.id LIMIT 21 OFFSET ?'
    with connect_database() as db:
        rows = db.execute(sql, [*args,*domains,offset]).fetchall()
        documents = [{'document_id':r[0], 'title':r[1], 'description':r[3], 'tags':[x[0] for x in db.execute('SELECT tag FROM markdown_tags WHERE document_id=? ORDER BY tag LIMIT 50',(r[2],))], 'domains':[x[0] for x in db.execute('SELECT domain FROM markdown_domains WHERE document_id=? ORDER BY domain LIMIT 50',(r[2],))]} for r in rows[:20]]
    return {'documents': documents, 'next_offset': offset+20 if len(rows)>20 else None}


def read_markdown(context, document_id, query, offset):
    scope,args = _scope(context)
    with connect_database() as db:
        row = db.execute('SELECT d.title,pm.paper_id FROM project_markdown pm JOIN markdown_documents d ON d.id=pm.document_id WHERE '+scope+' AND d.id=?', [*args,document_id]).fetchone()
        if not row:
            raise ValueError('文档不在当前范围')
        terms = re.findall(r'[^\W_]+', query, re.UNICODE)[:12]
        match = ' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
        chunks = db.execute('SELECT c.id,c.char_start,c.char_end,c.text FROM markdown_fts f JOIN markdown_chunks c ON c.id=f.rowid WHERE markdown_fts MATCH ? AND c.document_id=? ORDER BY rank, c.char_start LIMIT 5 OFFSET ?', (match,document_id,offset)).fetchall() if match else []
        if not chunks:
            # For unsegmented CJK, search substrings when FTS has no hits at all.
            has_fts = bool(match and db.execute('SELECT 1 FROM markdown_fts f JOIN markdown_chunks c ON c.id=f.rowid WHERE markdown_fts MATCH ? AND c.document_id=? LIMIT 1', (match, document_id)).fetchone())
            if not has_fts:
                fallback = [t for t in terms if len(t) >= 2]
                if fallback:
                    clauses = ' OR '.join('instr(text, ?) > 0' for _ in fallback)
                    chunks = db.execute('SELECT id,char_start,char_end,text FROM markdown_chunks WHERE document_id=? AND ('+clauses+') ORDER BY char_start LIMIT 5 OFFSET ?', [document_id,*fallback,offset]).fetchall()
        if not chunks and not (match and db.execute('SELECT 1 FROM markdown_fts f JOIN markdown_chunks c ON c.id=f.rowid WHERE markdown_fts MATCH ? AND c.document_id=? LIMIT 1', (match, document_id)).fetchone()):
            # Fall back to sequential reading if neither lexical method found a match.
            found_substring = bool(terms and db.execute('SELECT 1 FROM markdown_chunks WHERE document_id=? AND (' + ' OR '.join('instr(text, ?) > 0' for _ in terms) + ') LIMIT 1', [document_id, *terms]).fetchone())
            if not found_substring:
                chunks = db.execute('SELECT id,char_start,char_end,text FROM markdown_chunks WHERE document_id=? ORDER BY char_start LIMIT 5 OFFSET ?', (document_id,offset)).fetchall()
        return [dict(id=f'md_{document_id}_{r[1]}', title=row[0], text=r[3], source_level='local_markdown', char_start=r[1], char_end=r[2], document_id=document_id, **({'paper_id':row[1]} if row[1] is not None else {})) for r in chunks]
