"""Single worker that executes retrieval jobs in order."""

import argparse
import json
import logging
import re
import time
from threading import Event

from src import repository
from src.literature.retrieval import retrieve
from src.storage import initialize_storage
from src.logging_setup import configure_logging, safe_stack
def run_once():
    task = repository.claim_task()
    if task is None:
        return False
    log = logging.getLogger('tufa.worker')
    started = time.monotonic()
    log.info('task_start run_id=%s project_id=%s source=%s collection_id=%s', task['id'], task['project_id'], task['source'], task.get('collection_id'))
    try:
        if task.get('kind') == 'chat':
            from src.literature.workflow import run
            content, evidence = run(task)
            repository.finish_chat_task(task['id'], content, evidence)
        else:
            result = retrieve(task["project_id"], task["query"], task["source"])
            repository.finish_task(task["id"], result)
        log.info('task_complete run_id=%s elapsed_ms=%.1f', task['id'], (time.monotonic()-started)*1000)
    except Exception as exc:
        log.error('task_failed run_id=%s type=%s elapsed_ms=%.1f stack=%s', task['id'], type(exc).__name__, (time.monotonic()-started)*1000, safe_stack(exc))
        message = str(exc)
        safe_errors = {
            '请先在设置中配置模型地址和模型名称',
            '模型服务连接失败或超时，请检查设置',
            '模型返回的结构化结果无效',
            'OpenAlex 请求失败；请检查网络或稍后重试',
            '网页检索失败，无法获取证据，请检查搜索设置或稍后重试。',
        }
        safe_http = re.fullmatch(r'(?:模型服务请求失败 \(HTTP [0-9]{3}\)|OpenAlex 请求失败 \(HTTP [0-9]{3}\)；请检查网络、额度或 OPENALEX_API_KEY)', message)
        if message not in safe_errors and not safe_http:
            message = '任务执行失败，请查看 worker 日志'
        repository.fail_task(task["id"], message)
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

    configure_logging('worker')
    repository.interrupt_running_tasks()
    if args.once:
        run_once()
        return
    logging.getLogger('tufa.worker').info('worker_ready')
    try:
        stop = Event()
        while True:
            if not run_once() and stop.wait(1):
                break
    except KeyboardInterrupt:
        logging.getLogger('tufa.worker').info('worker_stopped')


if __name__ == "__main__":
    main()
