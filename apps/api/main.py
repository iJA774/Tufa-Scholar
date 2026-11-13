"""Local API for projects, imported papers, and retrieval tasks."""
from contextlib import asynccontextmanager
import csv
import io
import logging
import os
import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from pypdf import PdfReader

from src.persistence import repository as repo
from src.infrastructure import settings
from src.integrations.model import discover
from src.persistence.storage import data_directory, initialize_storage
from src.infrastructure.logging_setup import configure_logging, close_logging, safe_stack, clear_application_logs


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_storage()
    configure_logging('api')
    try:
        yield
    finally:
        close_logging()


app = FastAPI(title='Tufa Scholar', lifespan=lifespan)


@app.middleware('http')
async def log_request(request: Request, call_next):
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        logging.getLogger('tufa.api').error('request_failed method=%s type=%s stack=%s', request.method, type(exc).__name__, safe_stack(exc))
        response = JSONResponse(status_code=500, content={'detail': '服务内部错误'})
    route = request.scope.get('route')
    path = getattr(route, 'path_format', '<unmatched>')
    allowed_ids = ('project_id', 'run_id', 'conversation_id', 'collection_id', 'paper_id', 'version_id', 'report_id')
    params = request.scope.get('path_params', {})
    ids = {}
    for name in allowed_ids:
        value = params.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            ids[name] = value
        elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 18:
            ids[name] = int(value)
    cleared_logs_request = request.method == 'DELETE' and request.url.path == '/settings/logs'
    if not (cleared_logs_request and response.status_code < 400):
        logging.getLogger('tufa.api').info('request method=%s path=%s ids=%s status=%s elapsed_ms=%.1f', request.method, path, ids, response.status_code, (time.monotonic()-started)*1000)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic error details can include input values, including submitted secrets.
    if request.url.path in ('/settings', '/settings/models'):
        return JSONResponse(status_code=422, content={'detail': '设置参数无效'})
    return await request_validation_exception_handler(request, exc)


class SettingsInput(BaseModel):
    web_search_engine: str | None = None
    tavily_api_key: str | None = Field(default=None, repr=False)
    infoquest_api_key: str | None = Field(default=None, repr=False)
    brave_search_api_key: str | None = Field(default=None, repr=False)
    serper_api_key: str | None = Field(default=None, repr=False)
    clear_tavily_api_key: bool = False
    clear_infoquest_api_key: bool = False
    clear_brave_search_api_key: bool = False
    clear_serper_api_key: bool = False
    searx_base_url: str | None = None
    wikipedia_lang: str | None = None
    openalex_api_key: str | None = Field(default=None, repr=False)
    clear_openalex_api_key: bool = False
    openai_api_key: str | None = Field(default=None, repr=False)
    clear_openai_api_key: bool = False
    openai_base_url: str | None = None
    openai_model: str | None = None


class ModelDiscoveryInput(BaseModel):
    base_url: str
    api_key: str | None = Field(default=None, repr=False)
    clear_local_key: bool = False


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ConversationInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class PaperInput(BaseModel):
    title: str = Field(min_length=1)
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    year: int | None = None
    authors: list[str] = []
    source: str | None = None
    open_access: bool | None = None
    pdf_url: str | None = None
    original_url: str | None = None


class RunInput(BaseModel):
    query: str = Field(min_length=1)
    source: Literal['all', 'local', 'openalex'] = 'all'
    conversation_id: int | None = None


class QuestionInput(BaseModel):
    question: str = Field(min_length=1)
    model_name: str | None = Field(default=None, min_length=1, max_length=300)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)
    source: Literal['auto', 'all', 'local', 'openalex', 'collection', 'web'] = 'auto'
    research_mode: Literal['auto', 'deep', 'quick'] = 'auto'
    conversation_id: int | None = None
    collection_id: int | None = None


class CollectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CollectionPaperInput(PaperInput):
    title: str | None = None
    paper_id: int | None = None


def require_project(project_id):
    if not repo.project_exists(project_id):
        raise HTTPException(404, '项目不存在')


@app.get('/health')
def health():
    return {'status': 'ok', 'database': initialize_storage()}


@app.get('/settings')
def get_settings():
    return settings.public_settings()


@app.put('/settings')
def put_settings(body: SettingsInput):
    try:
        return settings.update(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete('/settings/logs')
def delete_logs():
    try:
        return {'cleared_files': clear_application_logs()}
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(500, '无法清除本地日志') from None


@app.post('/settings/models')
def discover_models(body: ModelDiscoveryInput):
    url = body.base_url.strip()
    try:
        settings.validate_base_url(url)
        if not url:
            raise ValueError('请填写模型地址')
        current = settings.model_config()
        same_origin = bool(current['base_url'] and settings.origin(url) == settings.origin(current['base_url']))
        draft_key = None if body.clear_local_key else ((body.api_key or '').strip() or None)
        if same_origin:
            key = os.environ.get('OPENAI_API_KEY') or draft_key or (None if body.clear_local_key else current['api_key'])
        else:
            key = draft_key
        models = discover(url, key)
        secrets = [secret for secret in (current['api_key'], draft_key) if secret]
        if any(secret in model for model in models for secret in secrets):
            raise RuntimeError('模型服务返回的数据无效，请手动填写模型名称')
        return {'models': models}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from None


@app.get('/projects')
def projects():
    return repo.list_projects()


@app.get('/models')
def available_models():
    config = settings.model_config()
    if not config.get('base_url'):
        raise HTTPException(422, '请先在设置页保存模型服务地址和密钥')
    try:
        return {'models': discover(config['base_url'], config['api_key'], details=True)}
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from None


@app.post('/projects', status_code=201)
def create_project(body: ProjectInput):
    return repo.create_project(body.name)


@app.delete('/projects/{project_id}', status_code=204)
def delete_project(project_id: int):
    result = repo.delete_project(project_id)
    if result == 'missing':
        raise HTTPException(404, '项目不存在')
    if result == 'running':
        raise HTTPException(409, '项目有正在运行的任务，请等待完成后删除')
    return Response(status_code=204)


@app.get('/projects/{project_id}/conversations')
def conversations(project_id: int):
    require_project(project_id)
    return repo.list_conversations(project_id)


@app.get('/conversations')
def all_conversations():
    return repo.list_all_conversations()


@app.post('/projects/{project_id}/conversations', status_code=201)
def create_conversation(project_id: int, body: ConversationInput):
    require_project(project_id)
    return repo.create_conversation(project_id, body.title)


@app.get('/projects/{project_id}/papers')
def papers(project_id: int):
    require_project(project_id)
    return repo.list_papers(project_id)


@app.post('/projects/{project_id}/papers', status_code=201)
def save_paper(project_id: int, body: PaperInput):
    require_project(project_id)
    try:
        return repo.save_paper(project_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/projects/{project_id}/papers/import', status_code=201)
async def import_paper(project_id: int, file: UploadFile = File(...), title: str | None = Form(None), doi: str | None = Form(None), arxiv_id: str | None = Form(None)):
    require_project(project_id)
    content = await file.read()
    if not content.startswith(b'%PDF-'):
        raise HTTPException(422, '请上传有效的 PDF 文件')
    try:
        reader = PdfReader(BytesIO(content))
        pages = [(page.extract_text() or '').strip() for page in reader.pages]
        text = '\n'.join(pages).strip()
        pdf_title = str(reader.metadata.title or '').strip() if reader.metadata else ''
    except Exception as exc:
        raise HTTPException(422, '无法解析 PDF 文件') from exc
    digest = sha256(content).hexdigest()
    from src.persistence.storage import connect_database
    with connect_database() as db:
        duplicate = bool(repo.existing_version(db, digest))
    path = data_directory() / 'originals' / f'{digest}.pdf'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    metadata = {'title': (title or pdf_title or Path(file.filename or '文献.pdf').stem).strip(), 'doi': doi, 'arxiv_id': arxiv_id, 'source': 'upload'}
    if not metadata['title']:
        raise HTTPException(422, '文献标题不能为空')
    try:
        result = repo.save_paper(project_id, metadata, {'filename': Path(file.filename or 'paper.pdf').name, 'digest': digest, 'path': str(path), 'text': text, 'pages': pages, 'text_status': 'text_available' if text else 'no_extractable_text'})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result['duplicate'] = duplicate
    result['imported_text_status'] = 'text_available' if text else 'no_extractable_text'
    return result


@app.get('/paper-versions/{version_id}/file')
def get_file(version_id: int):
    version = repo.version_file(version_id)
    if not version or not Path(version['path']).is_file():
        raise HTTPException(404, '原文不存在')
    return FileResponse(version['path'], media_type='application/pdf', filename=version['filename'], content_disposition_type='inline')


@app.post('/projects/{project_id}/questions', status_code=201)
def ask_question(project_id: int, body: QuestionInput):
    if not body.question.strip():
        raise HTTPException(422, '问题不能为空')
    if (body.source == 'collection') != (body.collection_id is not None):
        raise HTTPException(422, '选择收藏夹来源时必须指定收藏夹')
    model_name = os.environ.get('OPENAI_MODEL') or (body.model_name or '').strip() or settings.model_config()['model']
    effort = body.reasoning_effort
    if effort:
        if not repo.project_exists(project_id):
            raise HTTPException(404, '项目不存在')
        try:
            models = available_models()['models']
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
            models = []
        selected = next((m for m in models if m['id'] == model_name), None)
        levels = selected['reasoning_efforts'] if selected and selected['reasoning_efforts'] else ['low', 'medium', 'high']
        if effort not in levels:
            raise HTTPException(422, '所选模型未声明支持该思考强度，请刷新模型列表或使用供应商默认')
    result = repo.create_question(
        project_id, body.conversation_id, body.question.strip(), body.source,
        body.collection_id, model_name, effort, research_mode=body.research_mode,
    )
    if result is None:
        raise HTTPException(404, '项目或对话不存在')
    return result


@app.get('/conversations/{conversation_id}/messages')
def conversation_messages(conversation_id: int):
    result = repo.list_messages(conversation_id)
    if result is None:
        raise HTTPException(404, '对话不存在')
    return result


@app.get('/projects/{project_id}/search')
def search_history(project_id: int, q: str):
    require_project(project_id)
    if not q.strip():
        raise HTTPException(422, '查询不能为空')
    return repo.search_project(project_id, q.strip())


@app.get('/reports/{report_id}')
def report_details(report_id: int):
    report = repo.get_report(report_id)
    if report is None:
        raise HTTPException(404, '报告不存在')
    return report


@app.get('/reports/{report_id}/markdown')
def report_markdown(report_id: int):
    report = repo.get_report(report_id)
    if report is None:
        raise HTTPException(404, '报告不存在')
    return Response(report['content'], media_type='text/markdown; charset=utf-8', headers={'Content-Disposition':f'attachment; filename="report-{report_id}.md"'})


@app.get('/reports/{report_id}/evidence.csv')
def report_csv(report_id: int):
    report = repo.get_report(report_id)
    if report is None:
        raise HTTPException(404, '报告不存在')
    output = io.StringIO(newline='')
    fields = ['id','paper_id','title','source_level','version_id','page','text','original_url','provider','retrieved_at','published_at','char_start','char_end']
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(report['evidence'])
    return Response('﻿' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':f'attachment; filename="evidence-{report_id}.csv"'})


@app.get('/projects/{project_id}/paper-search')
def paper_search(project_id: int, q: str, source: Literal['all','local','openalex'] = 'all'):
    require_project(project_id)
    if not q.strip(): raise HTTPException(422, '查询不能为空')
    from src.tools import ToolContext, ToolRegistry
    try:
        return ToolRegistry().invoke('search_papers', {'query': q.strip()}, context=ToolContext(project_id, source))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get('/projects/{project_id}/web-search')
def web_search(project_id: int, q: str = Query(min_length=1, max_length=2000)):
    require_project(project_id)
    if not q.strip():
        raise HTTPException(422, '查询不能为空')
    from src.tools import ToolContext, ToolRegistry
    try:
        return {'results': ToolRegistry().invoke('search_web', {'query': q.strip(), 'limit': 5}, context=ToolContext(project_id, 'web'))}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from None


@app.get('/projects/{project_id}/collections')
def get_collections(project_id: int):
    require_project(project_id)
    return repo.collections(project_id)


@app.post('/projects/{project_id}/collections', status_code=201)
def new_collection(project_id: int, body: CollectionInput):
    require_project(project_id)
    if not body.name.strip(): raise HTTPException(422, '名称不能为空')
    return repo.create_collection(project_id,body.name.strip())


@app.delete('/projects/{project_id}/collections/{collection_id}', status_code=204)
def remove_collection(project_id: int, collection_id: int):
    if not repo.delete_collection(project_id, collection_id):
        raise HTTPException(404, '收藏夹不存在')
    return Response(status_code=204)


@app.get('/projects/{project_id}/collections/{collection_id}/papers')
def collection_list(project_id: int, collection_id: int):
    result = repo.collection_papers(project_id,collection_id)
    if result is None: raise HTTPException(404, '收藏夹不存在')
    return result


@app.post('/projects/{project_id}/collections/{collection_id}/papers', status_code=201)
def collection_add(project_id: int, collection_id: int, body: CollectionPaperInput):
    if body.paper_id is not None:
        result = repo.collection_add_existing(project_id,collection_id,body.paper_id)
    else:
        if not body.title or not body.title.strip(): raise HTTPException(422, '文献标题不能为空')
        try:
            result = repo.save_paper(project_id,body.model_dump(exclude={'paper_id'}),collection_id=collection_id)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
    if result is None: raise HTTPException(404, '收藏夹或项目文献不存在')
    return result


@app.delete('/projects/{project_id}/collections/{collection_id}/papers/{paper_id}', status_code=204)
def collection_remove(project_id: int, collection_id: int, paper_id: int):
    if not repo.collection_remove(project_id,collection_id,paper_id): raise HTTPException(404, '收藏记录不存在')
    return Response(status_code=204)


@app.get('/projects/{project_id}/runs')
def list_runs(project_id: int, conversation_id: int | None = None):
    require_project(project_id)
    if conversation_id is not None and not repo.conversation_exists(project_id, conversation_id):
        raise HTTPException(404, '对话不存在')
    return repo.list_tasks(project_id, conversation_id)


@app.post('/projects/{project_id}/runs', status_code=201)
def create_run(project_id: int, body: RunInput):
    require_project(project_id)
    if body.conversation_id is not None and not repo.conversation_exists(project_id, body.conversation_id):
        raise HTTPException(404, '对话不存在')
    task_id = repo.create_task(project_id, body.conversation_id, body.query, body.source)
    return {'run_id': task_id, 'id': task_id}


@app.get('/runs/{run_id}')
def get_run(run_id: int):
    task = repo.get_task(run_id)
    if not task:
        raise HTTPException(404, '任务不存在')
    return task


@app.post('/runs/{run_id}/retry', status_code=201)
def retry_run(run_id: int):
    task = repo.get_task(run_id)
    if not task:
        raise HTTPException(404, '任务不存在')
    if task['status'] not in ('failed', 'interrupted'):
        raise HTTPException(409, '只有失败或中断的任务可以重新执行')
    return repo.retry_task(run_id)
