"""Single worker that executes retrieval jobs in order."""

import argparse
import json
import logging
from threading import Event

from src import repository
from src.literature.retrieval import retrieve
from src.storage import initialize_storage
def run_once():
    task = repository.claim_task()
    if task is None:
        return False
    try:
        result = retrieve(task["project_id"], task["query"], task["source"])
        repository.finish_task(task["id"], result)
    except Exception as exc:
        logging.error("Task %s failed: %s", task["id"], exc)
        repository.fail_task(task["id"], str(exc))
    return True

def main():
    parser = argparse.ArgumentParser(description="Tufa Scholar single worker")
    parser.add_argument("--check", action="store_true", help="Check storage and exit")
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit")
    args = parser.parse_args()
    status = initialize_storage()
    if args.check:
        print(json.dumps({"status": "ready", "database": status}, ensure_ascii=False))
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    repository.interrupt_running_tasks()
    if args.once:
        run_once()
        return
    logging.info("Worker ready; database: %s", status["path"])
    try:
        stop = Event()
        while True:
            if not run_once() and stop.wait(1):
                break
    except KeyboardInterrupt:
        logging.info("Worker stopped")


if __name__ == "__main__":
    main()
