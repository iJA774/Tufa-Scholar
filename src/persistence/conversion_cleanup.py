"""Safely stage generated conversion artifacts for a transactional reset."""
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from src.persistence.storage import connect_database, data_directory
from src.persistence import conversions


def _safe(path):
    return not path.is_symlink() and not os.path.isjunction(path)


def clear():
    base = data_directory()
    documents = base / 'documents'
    root = documents / 'conversions'
    legacy = base / 'conversions'
    for path in (base, documents, root, root / '.batches', legacy):
        if not _safe(path):
            raise OSError('unsafe directory')
    candidates = []
    # Walk without following links; reject links rather than leave misleading results.
    for tree in (root, legacy):
        if not tree.exists():
            continue
        for parent, dirs, files in os.walk(tree, followlinks=False):
            for name in dirs + files:
                if not _safe(Path(parent) / name):
                    raise OSError('unsafe conversion path')
            for name in files:
                path = Path(parent) / name
                relative = path.relative_to(tree)
                batch_area = tree == legacy or (relative.parts and relative.parts[0] == '.batches')
                if (not batch_area and (path.suffix.lower() == '.md' or re.fullmatch(r'\.[0-9a-f]{32}\.tmp', name))) or (
                    batch_area and re.fullmatch(r'[0-9a-f]{32}\.(?:md|json|zip|upload|tmp)', name)):
                    candidates.append(path)
    staging = base / ('.conversion-clear-' + uuid4().hex)
    moved = []
    with connect_database() as db:
        db.execute('BEGIN IMMEDIATE')
        counts = conversions.clear_derived(db)
        try:
            staging.mkdir()
            for index, path in enumerate(candidates):
                target = staging / str(index)
                path.replace(target)
                moved.append((path, target))
            db.commit()
        except BaseException:
            db.rollback()
            for path, target in reversed(moved):
                target.replace(path)
            if staging.exists(): staging.rmdir()
            raise
    try:
        shutil.rmtree(staging)
        pending = False
    except OSError:
        pending = True
    return {**counts, 'files': len(moved), 'cleanup_pending': pending}
