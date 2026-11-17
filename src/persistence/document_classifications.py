"""Classification metadata and searchable domain/tag relationships."""
from src.persistence.storage import connect_database


def upsert_classification(path, digest, classification):
    data = classification.model_dump() if hasattr(classification, 'model_dump') else dict(classification)
    description = data['description']
    if not isinstance(description, str) or len(description) > 20:
        raise ValueError('description must contain at most 20 characters')
    domains = list(dict.fromkeys(x.strip() for x in data['domains'] if x.strip()))
    tags = list(dict.fromkeys(x.strip() for x in data['tags'] if x.strip()))
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO document_classifications(path, digest, description) VALUES (?, ?, ?) ON CONFLICT(path) DO UPDATE SET digest=excluded.digest, description=excluded.description', (path, digest, description))
        db.execute('DELETE FROM document_domains WHERE path=?', (path,))
        db.execute('DELETE FROM document_tags WHERE path=?', (path,))
        db.executemany('INSERT INTO document_domains(path, domain) VALUES (?, ?)', [(path, x) for x in domains])
        db.executemany('INSERT INTO document_tags(path, tag) VALUES (?, ?)', [(path, x) for x in tags])
        db.commit()


def _read_classification(db, row):
    if row is None:
        return None
    path, digest, description = row
    return {'path': path, 'digest': digest, 'description': description,
            'domains': [r[0] for r in db.execute('SELECT domain FROM document_domains WHERE path=? ORDER BY rowid', (path,))],
            'tags': [r[0] for r in db.execute('SELECT tag FROM document_tags WHERE path=? ORDER BY rowid', (path,))]}


def get_classification(path):
    with connect_database() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT path, digest, description FROM document_classifications WHERE path=?', (path,)).fetchone()
        return _read_classification(db, row)


def get_classification_by_digest(digest):
    """Find identical content, choosing the lexically first path deterministically."""
    with connect_database() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT path, digest, description FROM document_classifications WHERE digest=? ORDER BY path LIMIT 1', (digest,)).fetchone()
        return _read_classification(db, row)
