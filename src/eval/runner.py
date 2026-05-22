"""Step 8 — evaluation runner and CLI.

Loads JSONL eval sets, computes aggregate metrics, and appends timestamped
results to ``data/eval/results/``. Retrieval/mapping callables are injected so
this runner stays decoupled from Steps 5-7.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import typer

from src.eval.metrics import column_f1, mean_reciprocal_rank, ndcg_at_k, recall_at_k
from src.utils.logging import get_logger
from src.utils.paths import EVAL_DIR

log = get_logger(__name__)

RetrieveFn = Callable[[str], list[str]]
MapFn = Callable[[str], dict[str, str]]


def load_jsonl(path: Path) -> list[dict[str, object]]:
    """Load a JSONL file into a list of dicts.

    Args:
        path: Path to the JSONL file.

    Returns:
        Parsed records (empty list if the file is absent).
    """
    if not path.exists():
        log.warning("eval.dataset_missing", path=str(path))
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate_retrieval(
    dataset: list[dict[str, object]],
    retrieve: RetrieveFn,
    k: int = 10,
) -> dict[str, float]:
    """Compute aggregate retrieval metrics over a dataset.

    Args:
        dataset: Records with ``query`` and ``expected_event_ids``.
        retrieve: Function mapping a query to a ranked id list.
        k: Cutoff rank.

    Returns:
        Mean ``recall@k``, ``mrr``, ``ndcg@k``.
    """
    if not dataset:
        return {"recall@k": 0.0, "mrr": 0.0, "ndcg@k": 0.0, "n": 0}
    recalls, mrrs, ndcgs = [], [], []
    for record in dataset:
        query = str(record["query"])
        relevant = [str(x) for x in record.get("expected_event_ids", [])]
        retrieved = retrieve(query)
        recalls.append(recall_at_k(retrieved, relevant, k))
        mrrs.append(mean_reciprocal_rank(retrieved, relevant))
        ndcgs.append(ndcg_at_k(retrieved, relevant, k))
    n = len(dataset)
    return {
        "recall@k": round(sum(recalls) / n, 4),
        "mrr": round(sum(mrrs) / n, 4),
        "ndcg@k": round(sum(ndcgs) / n, 4),
        "n": n,
    }


def evaluate_mapping(
    dataset: list[dict[str, object]],
    map_fn: MapFn,
) -> dict[str, float]:
    """Compute aggregate column-level F1 for schema mapping.

    Args:
        dataset: Records with ``source_file`` and ``expected`` column maps.
        map_fn: Function mapping a source file to predicted column maps.

    Returns:
        Mean precision/recall/F1.
    """
    if not dataset:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "n": 0}
    f1s, precisions, recalls = [], [], []
    for record in dataset:
        expected = {str(k): str(v) for k, v in dict(record["expected"]).items()}
        predicted = map_fn(str(record["source_file"]))
        scores = column_f1(predicted, expected)
        f1s.append(scores["f1"])
        precisions.append(scores["precision"])
        recalls.append(scores["recall"])
    n = len(dataset)
    return {
        "precision": round(sum(precisions) / n, 4),
        "recall": round(sum(recalls) / n, 4),
        "f1": round(sum(f1s) / n, 4),
        "n": n,
    }


def save_results(results: dict[str, object], config: str) -> Path:
    """Append a timestamped results file to ``data/eval/results/``.

    Args:
        results: The metrics dict to persist.
        config: A short config label for the filename.

    Returns:
        Path to the written results file.
    """
    results_dir = EVAL_DIR / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = results_dir / f"{ts}_{config}.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("eval.results_saved", path=str(path))
    return path


app = typer.Typer(help="Evaluation runner.")


@app.command()
def retrieval(top_k: int = typer.Option(10, help="Cutoff rank.")) -> None:
    """Evaluate retrieval against ``data/eval/retrieval_eval.jsonl``.

    A stub retriever is used until Steps 6-7 are wired in (see TODO).
    """
    dataset = load_jsonl(EVAL_DIR / "retrieval_eval.jsonl")
    # TODO(real-data): inject the real Qdrant/graph retriever here.
    metrics = evaluate_retrieval(dataset, retrieve=lambda _q: [], k=top_k)
    typer.echo(json.dumps(metrics, indent=2))


@app.command()
def mapping(version: str = typer.Option(..., help="Form version label.")) -> None:
    """Evaluate schema mapping against ``data/eval/mapping_eval.jsonl``."""
    dataset = load_jsonl(EVAL_DIR / "mapping_eval.jsonl")
    dataset = [r for r in dataset if r.get("form_version") == version]
    # TODO(real-data): inject the real schema-mapper predictor here.
    metrics = evaluate_mapping(dataset, map_fn=lambda _f: {})
    typer.echo(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    app()
