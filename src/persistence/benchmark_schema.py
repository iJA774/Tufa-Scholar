"""Schema for benchmark batches and their independently checkpointed attempts."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    config TEXT NOT NULL,
    credentials TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS benchmark_tasks (
    run_id TEXT NOT NULL REFERENCES benchmark_runs(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    language TEXT NOT NULL,
    topic TEXT NOT NULL,
    PRIMARY KEY(run_id, task_id)
);
CREATE TABLE IF NOT EXISTS benchmark_stages (
    run_id TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(run_id, task_id, phase),
    FOREIGN KEY(run_id, task_id) REFERENCES benchmark_tasks(run_id, task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS benchmark_stages_status ON benchmark_stages(run_id, phase, status);
CREATE TABLE IF NOT EXISTS benchmark_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    phase TEXT NOT NULL,
    research_id INTEGER REFERENCES tasks(id),
    status TEXT NOT NULL,
    started REAL NOT NULL,
    heartbeat REAL NOT NULL,
    ended REAL,
    error TEXT,
    result TEXT,
    FOREIGN KEY(run_id, task_id) REFERENCES benchmark_tasks(run_id, task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS benchmark_attempts_run ON benchmark_attempts(run_id, task_id, id);
CREATE TABLE IF NOT EXISTS benchmark_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES benchmark_attempts(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    phase TEXT NOT NULL,
    started REAL NOT NULL,
    elapsed REAL,
    status INTEGER,
    usage TEXT
);
CREATE INDEX IF NOT EXISTS benchmark_calls_attempt ON benchmark_calls(attempt_id);
"""
