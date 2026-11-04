"""SQLite connection setup and the scaffold's storage readiness check."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def data_directory() -> Path:
    path = Path(os.environ.get("TUFA_DATA_DIR", "data"))
    return path if path.is_absolute() else PROJECT_ROOT / path


@contextmanager
def connect_database():
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "research.db")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        yield connection
    finally:
        connection.close()


def initialize_storage() -> dict:
    directory = data_directory()
    for child in ("originals", "reports"):
        (directory / child).mkdir(parents=True, exist_ok=True)
    with connect_database() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL, doi TEXT UNIQUE, arxiv_id TEXT UNIQUE, abstract TEXT, year INTEGER, authors TEXT NOT NULL DEFAULT '[]', source TEXT, open_access INTEGER, pdf_url TEXT, original_url TEXT);
        CREATE TABLE IF NOT EXISTS project_papers (project_id INTEGER NOT NULL REFERENCES projects(id), paper_id INTEGER NOT NULL REFERENCES papers(id), PRIMARY KEY(project_id, paper_id));
        CREATE TABLE IF NOT EXISTS paper_versions (id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id), filename TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, path TEXT NOT NULL, text TEXT NOT NULL, text_status TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(title, abstract, full_text);
        CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), conversation_id INTEGER REFERENCES conversations(id), query TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, result TEXT, error TEXT);
        """)
        connection.commit()
        # Exercise FTS5 indexing/querying without inserting test papers.
        connection.execute("CREATE VIRTUAL TABLE temp.fts_probe USING fts5(content)")
        connection.execute("INSERT INTO fts_probe(content) VALUES ('readiness')")
        fts5 = connection.execute(
            "SELECT count(*) FROM fts_probe WHERE fts_probe MATCH 'readiness'"
        ).fetchone()[0] == 1
        return {
            "path": str(directory / "research.db"),
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
            "fts5": fts5,
        }
