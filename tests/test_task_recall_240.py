import json
from pathlib import Path

from benchmarks.bench_task_recall_240 import CORPUS


def test_issue_240_task_corpus_is_versioned_and_explicit_about_external_gates() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["schema"] == "simplicio.fast.delivery-task-corpus/v1"
    assert corpus["scope"] == "frozen-source-task-fixture"
    assert len(corpus["tasks"]) == 4
    assert all(Path(item["expected_files"][0]).is_absolute() is False for item in corpus["tasks"])
    assert all(item["text"] and item["expected_files"] for item in corpus["tasks"])
