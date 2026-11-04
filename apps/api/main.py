"""Local API for projects, imported papers, and retrieval tasks."""
from contextlib import asynccontextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader

from src import repository as repo
from src.storage import data_directory, initialize_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_storage()
    yield


app = FastAPI(title='Tufa Scholar', lifespan=lifespan)


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


def require_project(project_id):
    if not repo.project_exists(project_id):
        raise HTTPException(404, '项目不存在')


@app.get('/health')
def health():
    return {'status': 'ok', 'database': initialize_storage()}


@app.get('/projects')
def projects():
    return repo.list_projects()


@app.post('/projects', status_code=201)
def create_project(body: ProjectInput):
    return repo.create_project(body.name)


@app.get('/projects/{project_id}/conversations')
def conversations(project_id: int):
    require_project(project_id)
    return repo.list_conversations(project_id)


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
        text = '\n'.join(page.extract_text() or '' for page in reader.pages).strip()
        pdf_title = str(reader.metadata.title or '').strip() if reader.metadata else ''
    except Exception as exc:
        raise HTTPException(422, '无法解析 PDF 文件') from exc
    digest = sha256(content).hexdigest()
    from src.storage import connect_database
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
        result = repo.save_paper(project_id, metadata, {'filename': Path(file.filename or 'paper.pdf').name, 'digest': digest, 'path': str(path), 'text': text, 'text_status': 'text_available' if text else 'no_extractable_text'})
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
