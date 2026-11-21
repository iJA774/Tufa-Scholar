"""Single worker that executes retrieval jobs in order."""

import argparse
import html
import json
import logging
import re
import time

from src.benchmark import scheduler as benchmark_scheduler
from src.persistence import benchmarks as benchmark_store
from threading import Event

from src.persistence import repository
from src.tools import ToolContext, ToolRegistry
from src.persistence.storage import initialize_storage
from src.infrastructure.logging_setup import configure_logging, safe_stack
from src.infrastructure.diagnostics import diagnostic_scope
from src.integrations.model import ModelServiceError, StructuredOutputError, track_token_usage


def run_once(benchmark_task_id=None):
    task = repository.claim_task(benchmark_task_id)
    if task is None:
        return False
    log = logging.getLogger('tufa.worker')
    started = time.monotonic()
    log.info('task_start run_id=%s project_id=%s source=%s collection_id=%s', task['id'], task['project_id'], task['source'], task.get('collection_id'))
    try:
        if task.get('kind') == 'chat':
            from src.agents.literature import run
            run_id = task['id']
            research = {key: task.get(key) for key in (
                'query', 'project_id', 'source', 'paper_ids', 'model_name', 'reasoning_effort', 'research_mode',
                'report_format', 'research_depth', 'prefer_primary_evidence', 'relevance_filter', 'network_search', 'local_literature'
            )}
            def on_event(kind, data):
                if benchmark_task_id is not None:
                    data = {**data, 'timestamp': time.time()}
                repository.record_research_event(task['id'], kind, data)

            tags = {'suppress_artifacts': True} if benchmark_task_id is not None else {}
            snapshot_box = []
            stages = []
            def stage_changed(stage):
                stages.append(stage)
                repository.update_stage(task['id'], stage)
            with (
                diagnostic_scope(run_id=run_id, project_id=task['project_id'], **tags),
                track_token_usage(lambda total: repository.add_conversation_tokens(run_id, total)),
            ):
                if task.get('report_source_id'):
                    from src.agents.literature import replay_report
                    snapshot = repository.get_report_snapshot(task['report_source_id'])
                    if snapshot is None:
                        raise ValueError('报告快照不存在')
                    content, evidence = replay_report(snapshot, task['report_format'], task['model_name'],
                                                      on_stage=stage_changed, on_event=on_event)
                    report_model = task['model_name']
                else:
                    history = [] if task['source'] == 'collection' else repository.task_history(task['id'])
                    kwargs = dict(history=history, on_stage=stage_changed,
                                  on_subtask=lambda note: repository.record_subtask(task['id'], note), on_event=on_event)
                    if benchmark_task_id is None:
                        kwargs['on_report_snapshot'] = snapshot_box.append
                    content, evidence = run(research, **kwargs)
                    snapshot = snapshot_box[0] if snapshot_box else None
                    report_model = snapshot['model'] if snapshot else None
            if benchmark_task_id is None and 'reporter' in stages:
                if report_model:
                    label = {'academic': '学术综述', 'exploratory': '探索发现', 'brief': '决策简报', 'explainer': '通俗解读'}[task['report_format']]
                    safe_model = html.escape(re.sub(r'[\r\n]+', ' ', report_model))
                    safe_model = re.sub(r'([\\`*_\[\]<>])', r'\\\1', safe_model)
                    content = f'**回答风格：** {label}  \n**报告模型：** {safe_model}\n\n' + content
                else:
                    content = '**报告状态：** 终稿未生成，以下为降级内容。\n\n' + content
            repository.finish_chat_task(task['id'], content, evidence, snapshot=snapshot if not task.get('report_source_id') else None, report_model=report_model)
        else:
            result = ToolRegistry().invoke('search_papers', {'query': task['query']}, context=ToolContext(task['project_id'], task['source']))
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
        if task.get('kind') == 'chat':
            suggestions = {
                '请先在设置中配置模型地址和模型名称': '请在设置中填写模型服务地址和模型名称后重试。',
                '模型服务连接失败或超时，请检查设置': '请检查模型服务地址和网络连接，或稍后重试。',
                '模型返回的结构化结果无效': '请更换支持结构化输出的模型，或降低研究深度后重试。',
            }
            model_failures = {
                'timeout': ('模型服务响应超时。', '请稍后重试，或降低研究深度后重试。'),
                'network': ('无法连接模型服务。', '请检查模型服务地址和网络连接后重试。'),
                'rate_limit': ('模型服务触发限流或额度不足。', '请检查服务额度，等待一段时间后重试。'),
                'auth': ('模型服务的密钥或访问权限不可用。', '请检查 API 密钥和模型访问权限后重试。'),
                'model_not_found': ('模型不存在或无法访问。', '请检查手动填写的模型 ID 及访问权限，或选择其他模型。'),
                'not_found': ('模型服务地址或模型不可用。', '请检查模型服务地址和手动填写的模型 ID。'),
                'context_length': ('输入超出模型长度限制。', '请缩短问题或降低研究深度后重试。'),
                'bad_request': ('模型服务无法处理本次请求。', '请检查模型设置，或更换支持所需功能的模型。'),
            }
            output_failures = {
                'length': ('模型输出达到长度限制。', '请缩短问题或降低研究深度后重试。'),
                'filtered': ('模型输出受到服务限制。', '请调整问题表述或更换模型后重试。'),
                'empty': ('模型返回了空内容。', '请稍后重试，或更换模型。'),
            }
            if isinstance(exc, ModelServiceError):
                message, suggestion = model_failures.get(exc.category, ('模型服务暂时不可用。', '请检查模型设置，或稍后重试。'))
            elif isinstance(exc, StructuredOutputError):
                message, suggestion = output_failures.get(exc.category, ('模型返回的内容无法使用。', '请更换模型或稍后重试。'))
            elif safe_http and message.startswith('模型服务'):
                message = '模型服务未能处理本次请求。'
                suggestion = '请检查模型服务地址、密钥、模型 ID 和访问权限后重试。'
            elif safe_http:
                message = '文献检索服务未能处理本次请求。'
                suggestion = '请检查网络和检索服务额度，或稍后重试。'
            else:
                suggestion = suggestions.get(message, '请检查网络和相关服务设置，或稍后重试。')
            repository.fail_task(task['id'], message, event={'message': message, 'suggestion': suggestion})
        else:
            repository.fail_task(task['id'], message)
    return True


def run_benchmark_attempt(attempt_id):
    from src.benchmark.evaluator import ScoringFailure
    from src.benchmark.registry import adapter_for
    from src.benchmark.results import saved_report, redact
    from src.infrastructure.execution import execution_scope
    from src.infrastructure.settings import research_settings

    attempt = benchmark_store.get_attempt(attempt_id)
    if not attempt or attempt['status'] != 'running':
        return 1
    batch = benchmark_store.get_run(attempt['run_id'], private=True)
    config, credentials = batch['config'], batch['credentials']
    adapter = adapter_for(config["dataset"]["id"])
    monitor = benchmark_scheduler.Monitor(attempt_id, config['budget'])
    task = next(row for row in benchmark_store.task_rows(batch['id']) if row['task_id'] == attempt['task_id'])
    try:
        if attempt['phase'] == 'generate':
            configure_logging('worker')
            with research_settings(credentials['research']), execution_scope(monitor):
                if not run_once(attempt['research_id']):
                    return 1
            report = saved_report(batch['id'], task['task_id'])
            if report:
                row, diagnostics = adapter.report_record(task, report)
                sources = {item.get('original_url') or item.get('paper_id') or item.get('id') for item in report['evidence']}
                benchmark_store.finish(attempt_id, result={'article': row['article'], 'citation_diagnostics': diagnostics,
                                                           'report_metrics': {'characters': len(report['content']),
                                                                              'sources': len(sources)}})
            return 0
        report = saved_report(batch['id'], task['task_id'])
        row, diagnostics = adapter.report_record(task, report)
        result = adapter.score(attempt['phase'], task, row, diagnostics, config['judge'], credentials['judge'], monitor)
        result = redact(result, credentials)
        benchmark_store.finish(attempt_id, result=result, error=result.get('error'))
    except benchmark_scheduler.BudgetReached:
        return 75
    except ScoringFailure as exc:
        benchmark_store.finish(attempt_id, error=str(exc))
    except Exception as exc:
        logging.getLogger('tufa.worker').error('benchmark_failed attempt_id=%s type=%s', attempt_id, type(exc).__name__)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Tufa Scholar single worker")
    parser.add_argument("--check", action="store_true", help="Check storage and exit")
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit")
    parser.add_argument("--benchmark-attempt", nargs=2, type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.benchmark_attempt:
        attempt_id, parent_pid = args.benchmark_attempt
        with benchmark_scheduler.child_lifetime(parent_pid):
            raise SystemExit(run_benchmark_attempt(attempt_id))
    status = initialize_storage()
    if args.check:
        print(json.dumps({"status": "ready", "database": status}, ensure_ascii=False))
        return

    configure_logging('worker')
    repository.interrupt_running_tasks()
    benchmark_store.recover()
    if args.once:
        run_once() or benchmark_scheduler.run_once()
        return
    logging.getLogger('tufa.worker').info('worker_ready')
    try:
        stop = Event()
        while True:
            if not run_once() and not benchmark_scheduler.run_once() and stop.wait(1):
                break
    except KeyboardInterrupt:
        logging.getLogger('tufa.worker').info('worker_stopped')


if __name__ == "__main__":
    main()
