"""Browser-only fixture: real API/SQLite; search, model catalog and task execution are simulated.

Run: python -m scripts.fixture_api --data-dir <empty isolated directory> [--port 18180] [--local-config]
POST /__fixture/advance claims the oldest pending task, then marks it running;
POST it again completes it. Queries containing 'fail' fail once; retry succeeds.
Search queries containing 'empty' return no results, 'fail' simulate an upstream error.
Quick tasks display a dispatcher stage and no evidence; deep tasks display a researcher stage.
These are UI fixture labels and results, not execution of the graph or worker.

--local-config stores fake model/search values in the isolated SQLite settings table,
so the settings UI can be edited without environment values masking saved settings.
"""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from apps.api.main import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--port', type=int, default=18180)
    parser.add_argument('--local-config', action='store_true', help='store fake settings in SQLite instead of environment variables')
    args = parser.parse_args()
    directory = Path(args.data_dir).resolve()
    protected = (ROOT / 'data').resolve()
    if directory == protected or protected in directory.parents or directory in protected.parents:
        parser.error('data-dir must be isolated from the default data directory')
    if directory.exists() and any(directory.iterdir()):
        parser.error('data-dir must be empty')
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['TUFA_DATA_DIR'] = str(directory)
    # Keep inherited credentials and provider settings out of this disposable fixture.
    for name in (
        'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENALEX_API_KEY',
        'SEARCH_API', 'SEARX_HOST', 'TAVILY_API_KEY', 'INFOQUEST_API_KEY',
        'BRAVE_SEARCH_API_KEY', 'SERPER_API_KEY', 'WIKIPEDIA_LANG',
    ):
        os.environ.pop(name, None)
    if not args.local_config:
        os.environ['OPENAI_BASE_URL'] = 'https://fixture.invalid/v1'
        os.environ['OPENAI_MODEL'] = 'fixture-model'

    import uvicorn
    import apps.api.main as api
    import src.literature.retrieval as retrieval
    import src.tools.search as search
    from fastapi import HTTPException
    from src.persistence import repository as repo
    from src.persistence.storage import initialize_storage

    initialize_storage()
    if args.local_config:
        from src.infrastructure import settings
        settings.update({
            'openai_api_key': 'fixture-placeholder-key',
            'openai_base_url': 'https://fixture.invalid/v1',
            'openai_model': 'fixture-model',
            'web_search_engine': 'searx',
            'searx_base_url': 'https://fixture.invalid',
        })

    from scripts.verify_chat import make_pdf
    sample_pdf = directory / 'accuracy-fixture.pdf'
    sample_pdf.write_bytes(make_pdf())

    project = repo.create_project('注意力研究')
    other = repo.create_project('第二个项目')
    papers = [repo.save_paper(project['id'], {'title': title, 'abstract': abstract, 'doi': doi}) for title, abstract, doi in [
        ('Attention Is All You Need', 'Transformer 模型使用注意力机制。', '10.1234/attention'),
        ('Research Methods', '研究方法和实验结果摘要。', '10.1234/methods'),
        ('Evidence Study', '证据支持准确率 92%。', '10.1234/evidence'),
    ]]
    other_paper = repo.save_paper(other['id'], {'title': 'Other Project Paper', 'abstract': '另一个项目的文献。'})
    for name, paper in zip(('重点阅读', '待整理'), papers):
        collection = repo.create_collection(project['id'], name)
        repo.collection_add_existing(project['id'], collection['id'], paper['id'])
    def abstract_evidence(paper):
        return {'id': 'a1', 'paper_id': paper['id'], 'title': paper['title'],
                'source_level': 'abstract', 'version_id': None, 'page': None, 'text': paper['abstract']}

    def seed_conversation(project_id, question_text, paper, response, research_mode='auto'):
        question = repo.create_question(project_id, None, question_text, 'local', research_mode=research_mode)
        repo.claim_task()
        evidence = [] if research_mode == 'quick' else [abstract_evidence(paper)]
        citation = ' [a1]' if evidence else ''
        repo.finish_chat_task(question['run_id'], response + citation, evidence)
        return question

    seed_conversation(project['id'], '历史问题：注意力机制', papers[0], '历史回答：注意力机制用于模型。', 'deep')
    seed_conversation(project['id'], '快速模式历史：注意力机制解释', papers[1], '快速模式直接回答：注意力机制可聚合上下文。', 'quick')
    seed_conversation(other['id'], '跨项目历史：证据支持', other_paper, '历史回答：另一个项目也有独立历史。', 'auto')

    def catalog(url, key, details=False):
        return ([{'id': 'fixture-model', 'reasoning_efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}]
                if details else ['fixture-model'])

    def outcome(query):
        if 'fail' in query.lower():
            raise RuntimeError('模拟检索失败')
        return 'empty' not in query.lower()

    def paper_search(query, limit=20):
        return [{'title': 'Fixture external paper', 'abstract': '模拟外部论文摘要。', 'doi': '10.1234/external',
                 'source': 'fixture', 'source_level': 'abstract'}] if outcome(query) else []

    def web_search(query, limit=5):
        return [{'title': 'Fixture webpage', 'url': 'https://example.org/fixture', 'content': '模拟网页搜索摘要。',
                 'source_level': 'web_snippet', 'provider': 'fixture', 'retrieved_at': '2026-11-06T00:00:00+00:00'}] if outcome(query) else []

    api.discover = catalog
    retrieval.search_works = paper_search
    search.search_web = web_search
    running = None

    @api.app.post('/__fixture/advance')
    def advance():
        nonlocal running
        if running is None:
            running = repo.claim_task()
            if running is None:
                raise HTTPException(404, '没有待处理任务')
            # This browser fixture simulates only the stage label; it does not run the graph.
            stage = {'quick': 'dispatcher', 'deep': 'researcher'}.get(running.get('research_mode'), 'dispatcher')
            repo.update_stage(running['id'], stage)
            return {'run_id': running['id'], 'status': 'running'}
        task, running = running, None
        if 'fail' in task['query'].lower() and not task.get('origin_run_id'):
            repo.fail_task(task['id'], '模拟任务失败，可重试')
            return {'run_id': task['id'], 'status': 'failed'}
        if task['kind'] == 'chat':
            if task.get('research_mode') == 'quick':
                evidence = []
            elif task['source'] == 'web':
                evidence = [{'id': 'w1', 'title': 'Fixture webpage', 'text': '模拟网页搜索摘要。',
                             'source_level': 'web_snippet', 'original_url': 'https://example.org/fixture',
                             'provider': 'fixture', 'retrieved_at': '2026-11-06T00:00:00+00:00'}]
            else:
                allowed = task.get('paper_ids') if task['source'] == 'collection' else None
                records = repo.project_evidence(task['project_id'], allowed) if allowed is not None else repo.project_evidence(task['project_id'])
                paper = next((item for item in records if item.get('abstract')), None)
                evidence = [abstract_evidence(paper)] if paper else []
            answer = '模拟直接回答：quick 模式不进入研究。' if task.get('research_mode') == 'quick' else '模拟回答：已完成科研问题。'
            repo.finish_chat_task(task['id'], answer + (' [w1]' if task['source'] == 'web' and evidence else ' [a1]' if evidence else ''), evidence)
        else:
            repo.finish_task(task['id'], {'query': task['query'], 'source': task['source'], 'results': [], 'warnings': []})
        return {'run_id': task['id'], 'status': 'succeeded'}

    print(f'Fixture API: http://127.0.0.1:{args.port}; PID: {os.getpid()}; isolated data: {directory}', flush=True)
    print(f'Sample PDF: {sample_pdf}', flush=True)
    uvicorn.run(api.app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
