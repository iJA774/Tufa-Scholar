"""Local benchmark API. The worker owns generation and scoring."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from src.benchmark import datasets, results, service
from src.benchmark.models import Action, CreateRun, Input
from src.benchmark.registry import adapter_for
from src.persistence import benchmarks as store
from pydantic import Field

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


class Comparison(Input):
    ids: list[UUID] = Field(min_length=2, max_length=10)


def command(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "评测批次或题目不存在") from None
    except store.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except datasets.BenchmarkError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("")
def installation(dataset_id: str = datasets.DATASET):
    return command(service.installation, dataset_id)


@router.get("/dataset/tasks")
def dataset_tasks(dataset_id: str = datasets.DATASET):
    return command(command(adapter_for, dataset_id).dataset)[1]


@router.post("/dataset/download")
def download(dataset_id: str = datasets.DATASET):
    return command(command(adapter_for, dataset_id).install_dataset)


@router.post("/dataset/import")
def import_dataset(file: UploadFile, dataset_id: str = datasets.DATASET):
    try:
        return command(command(adapter_for, dataset_id).install_dataset, file.file.read(datasets.MAX_BYTES + 1))
    finally:
        file.file.close()


@router.post("/scorer/install")
def install_scorer(dataset_id: str = datasets.DATASET):
    return command(command(adapter_for, dataset_id).install_scorer)


@router.get("/runs")
def history():
    return [command(results.snapshot, run_id, True) for run_id in store.run_ids()]


@router.post("/runs", status_code=201)
def create(body: CreateRun):
    return command(service.create, body)


@router.post("/compare")
def compare(body: Comparison):
    if len(set(body.ids)) < 2:
        raise HTTPException(422, "请选择至少两个不同批次")
    return command(results.compare, list(dict.fromkeys(map(str, body.ids))))


@router.get("/runs/{run_id}")
def run(run_id: UUID):
    return command(results.snapshot, str(run_id))


@router.post("/runs/{run_id}/actions")
def control(run_id: UUID, body: Action):
    return command(service.control, str(run_id), body)


@router.delete("/runs/{run_id}", status_code=204)
def remove(run_id: UUID):
    command(store.delete, str(run_id))
    return Response(status_code=204)


@router.get("/runs/{run_id}/tasks/{task_id}")
def task(run_id: UUID, task_id: int):
    return command(results.task_detail, str(run_id), task_id)


@router.get("/runs/{run_id}/export")
def export(run_id: UUID, format: Literal["jsonl", "json", "csv", "md", "zip"] = "zip"):
    media, content = command(results.exports, str(run_id), format)
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="benchmark-{run_id}.{format}"',
        "Cache-Control": "no-store",
    })
