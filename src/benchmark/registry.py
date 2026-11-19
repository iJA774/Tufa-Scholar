"""Dataset, report and evaluator adapters for the benchmark worker."""

from collections.abc import Callable
from dataclasses import dataclass

from src.benchmark import datasets, evaluator, reports


@dataclass(frozen=True)
class Adapter:
    id: str
    metrics: tuple[str, ...]
    identity: Callable
    dataset: Callable
    install_dataset: Callable
    install_scorer: Callable
    scorer_ready: Callable
    report_record: Callable
    score: Callable


ADAPTERS = {
    datasets.DATASET: Adapter(datasets.DATASET, ("race", "fact"), datasets.identity, datasets.dataset,
                              datasets.install_dataset, datasets.install_scorer, datasets.scorer_ready,
                              reports.record, evaluator.evaluate),
}


def adapter_for(dataset_id):
    try:
        return ADAPTERS[dataset_id]
    except KeyError:
        raise datasets.BenchmarkError("不支持此 Benchmark 数据集") from None
