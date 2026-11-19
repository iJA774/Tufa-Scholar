"""SQLite transitions for benchmark runs and their linked normal research tasks."""

from contextlib import contextmanager
import json
import time

from src.persistence import repository
from src.persistence.storage import connect_database

GENERATE = "generate"
ACTIVE = ("pending", "generating", "evaluating", "pausing")
TERMINAL = ("completed", "partial", "failed", "cancelled")
UNFINISHED = ("pending", "running")


class Conflict(ValueError):
    pass


@contextmanager
def transaction():
    with connect_database() as db:
        db.row_factory = sqlite_row
        with db:
            db.execute("BEGIN IMMEDIATE")
            yield db



@contextmanager
def reader(db=None):
    if db is not None:
        yield db
        return
    with connect_database() as session:
        session.row_factory = sqlite_row
        yield session

def sqlite_row(cursor, values):
    return {column[0]: value for column, value in zip(cursor.description, values)}


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def decode(row, *fields):
    if row:
        for field in fields:
            if row[field] is not None:
                row[field] = json.loads(row[field])
    return row


def get_run(run_id, *, private=False, session=None):
    with reader(session) as db:
        row = db.execute("SELECT * FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
    if row:
        if private:
            decode(row, "credentials")
        else:
            del row["credentials"]
        decode(row, "config")
    return row


def create(run_id, name, selected, config, credentials):
    with transaction() as db:
        if db.execute("SELECT 1 FROM benchmark_runs WHERE id=?", (run_id,)).fetchone():
            return False
        project_id = db.execute("INSERT INTO projects(name) VALUES (?)", (name,)).lastrowid
        now = time.time()
        db.execute("""INSERT INTO benchmark_runs
            (id,project_id,name,status,config,credentials,created,updated)
            VALUES (?,?,?,'pending',?,?,?,?)""",
            (run_id, project_id, name, encode(config), encode(credentials), now, now))
        db.executemany("""INSERT INTO benchmark_tasks
            (run_id,task_id,position,prompt,language,topic) VALUES (?,?,?,?,?,?)""",
            [(run_id, row["id"], i, row["prompt"], row["language"], row["topic"]) for i, row in enumerate(selected)])
        db.executemany("""INSERT INTO benchmark_stages(run_id,task_id,phase,status) VALUES (?,?,?,?)""",
            [(run_id, row["id"], phase, "pending" if phase == GENERATE or phase in config["metrics"] else "disabled")
             for row in selected for phase in run_phases(config)])
    return True


def run_ids():
    with connect_database() as db:
        return [r[0] for r in db.execute("SELECT id FROM benchmark_runs ORDER BY created DESC")]


def run_phases(config):
    return (GENERATE, *config["available_metrics"])


def _task_rows(db, run_id):
    rows = db.execute("""SELECT t.*, (SELECT json_group_object(phase,status) FROM benchmark_stages s
        WHERE s.run_id=t.run_id AND s.task_id=t.task_id) AS stages
        FROM benchmark_tasks t WHERE t.run_id=? ORDER BY t.position""", (run_id,)).fetchall()
    for row in rows:
        row.update(json.loads(row.pop("stages")))
    return rows


def task_rows(run_id, *, session=None):
    with reader(session) as db:
        return _task_rows(db, run_id)


def attempts(run_id, task_id=None, *, details=False, report_metrics=False, session=None):
    columns = "a.*" if details else "a.id,a.run_id,a.task_id,a.phase,a.research_id,a.status,a.started,a.ended,a.error"
    if report_metrics:
        columns += ", json_extract(a.result, '$.report_metrics') AS report_metrics"
    condition = "" if task_id is None else "AND a.task_id=?"
    args = (run_id,) if task_id is None else (run_id, task_id)
    with reader(session) as db:
        rows = db.execute(f"""SELECT {columns},t.stage,t.subtasks
            FROM benchmark_attempts a LEFT JOIN tasks t ON t.id=a.research_id
            WHERE a.run_id=? {condition} ORDER BY a.id""", args).fetchall()
    fields = ["subtasks"]
    if details:
        fields.append("result")
    if report_metrics:
        fields.append("report_metrics")
    return [decode(row, *fields) for row in rows]


def scores(run_id, *, session=None):
    with reader(session) as db:
        rows = db.execute("""SELECT task_id,phase,result FROM benchmark_attempts
            WHERE run_id=? AND phase<>'generate' AND result IS NOT NULL ORDER BY id""", (run_id,)).fetchall()
    return {(r["task_id"], r["phase"]): json.loads(r["result"])["metrics"] for r in rows}


def calls(run_id, task_id=None, *, session=None):
    condition = "" if task_id is None else "AND a.task_id=?"
    args = (run_id,) if task_id is None else (run_id, task_id)
    with reader(session) as db:
        rows = db.execute(f"""SELECT c.id,c.phase,c.role,c.started,c.elapsed,c.status,c.usage,
            a.id AS attempt_id,a.task_id FROM benchmark_calls c JOIN benchmark_attempts a ON a.id=c.attempt_id
            WHERE a.run_id=? {condition} ORDER BY c.id""", args).fetchall()
    return [decode(row, "usage") for row in rows]


def state(db, run_id, status, reason=None):
    db.execute("UPDATE benchmark_runs SET status=?, reason=?, updated=? WHERE id=?",
               (status, reason, time.time(), run_id))


def reconcile(db, run_id):
    run = db.execute("SELECT status,config FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
    if db.execute("SELECT 1 FROM benchmark_attempts WHERE run_id=? AND status='running'", (run_id,)).fetchone():
        return
    if run["status"] == "pausing":
        state(db, run_id, "paused")
        return
    if run["status"] not in ACTIVE:
        return
    config = json.loads(run["config"])
    scoring = config["available_metrics"]
    score_only = config["score_only"]
    tasks = _task_rows(db, run_id)
    if any(t[GENERATE] == "succeeded" and any(t[p] in UNFINISHED for p in (score_only or scoring)) for t in tasks):
        return
    if any(t[GENERATE] in UNFINISHED or (t[GENERATE] == "succeeded" and any(t[p] in UNFINISHED for p in scoring))
           for t in tasks):
        if score_only:
            state(db, run_id, "paused", "selected_scores_finished")
        return
    successful = sum(t[GENERATE] == "succeeded" for t in tasks)
    complete = all(t[GENERATE] == "succeeded" and all(t[p] in ("succeeded", "disabled") for p in scoring)
                   for t in tasks)
    state(db, run_id, "completed" if complete else "partial" if successful else "failed")


def change(run_id, action, *, task_id=None, phases=(), config=None, credentials=None):
    with transaction() as db:
        run = db.execute("SELECT * FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(run_id)
        previous = run["status"]
        if config is not None and json.loads(run["config"]) != config[0]:
            raise Conflict("批次配置已更新，请刷新后重试")
        busy = db.execute("SELECT 1 FROM benchmark_attempts WHERE run_id=? AND status='running'", (run_id,)).fetchone()
        if action == "pause" and previous in ACTIVE:
            state(db, run_id, "pausing" if busy else "paused")
        elif action == "resume" and previous in ("paused", "interrupted"):
            state(db, run_id, "pending")
        elif action == "stop" and previous not in TERMINAL:
            state(db, run_id, "cancelled")
            db.execute("UPDATE benchmark_stages SET status='skipped' WHERE run_id=? AND status='pending'", (run_id,))
        elif action == "skip" and previous not in TERMINAL:
            condition = "WHERE run_id=? AND task_id=?"
            stages = db.execute(f"SELECT status FROM benchmark_stages {condition}", (run_id, task_id)).fetchall()
            if not stages or any(row["status"] == "running" for row in stages):
                raise Conflict("请选择未在执行的任务")
            db.execute(f"UPDATE benchmark_stages SET status='skipped' {condition} AND status='pending'",
                       (run_id, task_id))
            reconcile(db, run_id)
        elif action == "retry" and previous in ("partial", "failed") and not busy:
            selected = phases or run_phases(json.loads(run["config"]))
            count = sum(db.execute("""UPDATE benchmark_stages SET status='pending'
                WHERE run_id=? AND phase=? AND status='failed' AND (? IS NULL OR task_id=?)""",
                (run_id, phase, task_id, task_id)).rowcount for phase in selected)
            if not count:
                raise Conflict("没有匹配的失败阶段")
            state(db, run_id, "pending")
        elif action == "evaluate" and previous not in ACTIVE and not busy:
            eligible = """SELECT task_id FROM benchmark_stages
                WHERE run_id=? AND phase='generate' AND status='succeeded'"""
            if not db.execute(eligible, (run_id,)).fetchone():
                raise Conflict("批次没有已保存的研究报告")
            count = sum(db.execute(f"""UPDATE benchmark_stages SET status='pending' WHERE run_id=?
                AND phase=? AND task_id IN ({eligible}) AND status IN ('disabled','failed','pending','skipped')""",
                (run_id, phase, run_id)).rowcount for phase in phases)
            if not count:
                raise Conflict("所选评分全部已完成")
            state(db, run_id, "pending")
        else:
            raise Conflict("当前批次状态不支持该操作")
        if config is not None:
            db.execute("UPDATE benchmark_runs SET config=?, credentials=COALESCE(?,credentials) WHERE id=?",
                       (encode(config[1]), None if credentials is None else encode(credentials), run_id))


def budget_used(db, run_id):
    now = time.time()
    elapsed = db.execute("""SELECT COALESCE(SUM(COALESCE(ended,?) - started),0)
        FROM benchmark_attempts WHERE run_id=?""", (now, run_id)).fetchone()
    usage = db.execute("""SELECT COUNT(*) AS requests,
        COALESCE(SUM(json_extract(c.usage,'$.total_tokens')),0) AS tokens,
        COALESCE(SUM(json_extract(c.usage,'$.cost')),0) AS cost
        FROM benchmark_calls c JOIN benchmark_attempts a ON a.id=c.attempt_id
        WHERE a.run_id=? AND c.role IN ('agent','judge')""", (run_id,)).fetchone()
    return {"seconds": next(iter(elapsed.values())), **usage}


def budget_reason(used, budget):
    for used_key, limit_key in (("seconds", "total_seconds"), ("requests", "max_requests"),
                                ("tokens", "max_tokens"), ("cost", "max_cost")):
        if budget[limit_key] is not None and used[used_key] >= budget[limit_key]:
            return limit_key
    return None


def dispatch():
    with transaction() as db:
        if db.execute("""SELECT 1 FROM tasks t JOIN projects p ON p.id=t.project_id
            WHERE p.id NOT IN (SELECT project_id FROM benchmark_runs) AND t.status='pending' LIMIT 1""").fetchone():
            return None
        if db.execute("SELECT 1 FROM benchmark_attempts WHERE status='running' LIMIT 1").fetchone():
            return None
        runs = db.execute("""SELECT * FROM benchmark_runs
            WHERE status IN ('pending','generating','evaluating') ORDER BY created""").fetchall()
        for run in runs:
            run_id = run["id"]
            config = json.loads(run["config"])
            used = budget_used(db, run_id)
            reason = budget_reason(used, config["budget"])
            if reason:
                state(db, run_id, "paused", reason)
                continue
            tasks = _task_rows(db, run_id)
            for phase in config["score_only"] or run_phases(config):
                task = next((t for t in tasks if t[phase] == "pending" and
                             (phase == GENERATE or t[GENERATE] == "succeeded")), None)
                if task:
                    return start_attempt(db, run, task, phase, config, used)
            reconcile(db, run_id)
    return None


def start_attempt(db, run, task, phase, config, used):
    research_id = None
    if phase == GENERATE:
        research_id = repository.insert_question(db, run["project_id"], None, task["prompt"],
                                                local_literature=False, **config["research"])["run_id"]
    now = time.time()
    attempt_id = db.execute("""INSERT INTO benchmark_attempts(run_id,task_id,phase,research_id,status,started,heartbeat)
        VALUES (?,?,?,?,'running',?,?)""", (run["id"], task["task_id"], phase, research_id, now, now)).lastrowid
    db.execute("UPDATE benchmark_stages SET status='running' WHERE run_id=? AND task_id=? AND phase=?",
               (run["id"], task["task_id"], phase))
    state(db, run["id"], "generating" if phase == GENERATE else "evaluating")
    timeout = min(config["budget"]["task_seconds"], config["budget"]["total_seconds"] - used["seconds"])
    return attempt_id, timeout


def get_attempt(attempt_id):
    with connect_database() as db:
        db.row_factory = sqlite_row
        return db.execute("SELECT * FROM benchmark_attempts WHERE id=?", (attempt_id,)).fetchone()


def heartbeat(attempt_id):
    with connect_database() as db, db:
        db.execute("UPDATE benchmark_attempts SET heartbeat=? WHERE id=? AND status='running'", (time.time(), attempt_id))


def finish(attempt_id, *, result=None, error=None, interrupted=False, recovered=False):
    with transaction() as db:
        attempt = db.execute("SELECT * FROM benchmark_attempts WHERE id=? AND status='running'", (attempt_id,)).fetchone()
        if not attempt:
            return
        run_id, phase = attempt["run_id"], attempt["phase"]
        status = "pending" if interrupted else "failed" if error else "succeeded"
        status_on_stop = db.execute("SELECT status FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()["status"]
        if status_on_stop == "cancelled" and status == "pending":
            status = "skipped"
        if phase == GENERATE:
            research = db.execute("SELECT status FROM tasks WHERE id=?", (attempt["research_id"],)).fetchone()
            if research["status"] == "succeeded":
                status, error = "succeeded", None
            elif research["status"] == "failed":
                status, error = "failed", error or "research_failed"
            elif not interrupted and not error:
                status, error = "failed", "research_failed"
            if research["status"] in UNFINISHED:
                db.execute("UPDATE tasks SET status=?,stage=?,error=? WHERE id=?",
                           ("interrupted" if interrupted else "failed",
                            "interrupted" if interrupted else "failed", error, attempt["research_id"]))
        ended = attempt["heartbeat"] if recovered else time.time()
        db.execute("UPDATE benchmark_attempts SET status=?,ended=?,error=?,result=? WHERE id=?",
                   ("interrupted" if status == "pending" else status, ended, error,
                    None if result is None else encode(result), attempt_id))
        db.execute("UPDATE benchmark_stages SET status=? WHERE run_id=? AND task_id=? AND phase=?",
                   (status, run_id, attempt["task_id"], phase))
        reconcile(db, run_id)
        if error == "budget_reached":
            current = db.execute("SELECT status,config FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
            if current["status"] in ACTIVE:
                budget = json.loads(current["config"])["budget"]
                state(db, run_id, "paused", budget_reason(budget_used(db, run_id), budget) or "budget_reached")


def recover():
    with connect_database() as db:
        pending = db.execute("SELECT id FROM benchmark_attempts WHERE status='running'").fetchall()
    for (attempt_id,) in pending:
        finish(attempt_id, interrupted=True, error="worker_interrupted", recovered=True)
    # Active batches resume automatically. Committed reports and scores survive
    # death between child completion and the parent observing its exit.


def begin_call(attempt_id, role, phase, budget):
    with transaction() as db:
        attempt = db.execute("SELECT run_id FROM benchmark_attempts WHERE id=? AND status='running'", (attempt_id,)).fetchone()
        if not attempt:
            return None
        run_id = attempt["run_id"]
        limit = budget_reason(budget_used(db, run_id), budget)
        if limit:
            return None
        return db.execute("INSERT INTO benchmark_calls(attempt_id,role,phase,started) VALUES (?,?,?,?)",
                          (attempt_id, role, phase, time.time())).lastrowid


def end_call(call_id, elapsed, status, usage):
    with connect_database() as db, db:
        db.execute("UPDATE benchmark_calls SET elapsed=?,status=?,usage=? WHERE id=?",
                   (elapsed, status, encode(usage), call_id))


def delete(run_id):
    with transaction() as db:
        run = db.execute("SELECT * FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise KeyError(run_id)
        busy = db.execute("SELECT 1 FROM benchmark_attempts WHERE run_id=? AND status='running'", (run_id,)).fetchone()
        if run["status"] in ACTIVE or busy:
            raise Conflict("请先停止批次并等待当前阶段结束")
        project_id = run["project_id"]
        db.execute("DELETE FROM benchmark_runs WHERE id=?", (run_id,))
        db.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE project_id=?)",
                   (project_id,))
        db.execute("DELETE FROM reports WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM conversations WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
