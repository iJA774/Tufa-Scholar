"""Independent local conversion batches; research task/evidence tables are unchanged."""
import json
import time
from .storage import connect_database

TERMINAL = {'completed', 'cancelled', 'interrupted'}
FILE_TERMINAL = {'succeeded', 'partial', 'failed', 'skipped', 'cancelled'}


def create(batch):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT count(*) FROM conversion_batches WHERE state NOT IN ('completed','cancelled','interrupted')").fetchone()[0] >= 4:
            raise ValueError('最多同时保留 4 个未完成批次，请先取消已有批次。')
        db.execute('INSERT INTO conversion_batches VALUES (?,?,?,?)', (batch['id'], batch['state'], time.time(), json.dumps(batch, ensure_ascii=False)))
        db.commit()


def get(batch_id):
    with connect_database() as db:
        row = db.execute('SELECT payload FROM conversion_batches WHERE id=?', (batch_id,)).fetchone()
        return json.loads(row[0]) if row else None


def update(batch_id, change):
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM conversion_batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            raise KeyError(batch_id)
        batch = json.loads(row[0])
        change(batch)
        batch['revision'] = batch.get('revision', 0) + 1
        batch['updated'] = time.time()
        if batch['state'] in TERMINAL and not batch.get('finished'):
            batch['finished'] = batch['updated']
        db.execute('UPDATE conversion_batches SET state=?,payload=? WHERE id=?', (batch['state'], json.dumps(batch, ensure_ascii=False), batch_id))
        db.commit()
        return batch


def active():
    with connect_database() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM conversion_batches WHERE state NOT IN ('completed','cancelled','interrupted') ORDER BY created")]


def end(batch, state, code, message):
    batch['state'] = state
    for item in batch['files']:
        if item['state'] not in FILE_TERMINAL:
            item.update(state='cancelled' if state == 'cancelled' else 'failed', code=code, message=message)


def public(batch):
    # Parser page text is held in sidecar files, never in polling payloads.
    result = dict(batch)
    result['counts'] = {s: sum(f['state'] == s for f in batch['files']) for s in FILE_TERMINAL}
    result['done'] = sum(result['counts'].values())
    result['total'] = len(batch['files'])
    return result
