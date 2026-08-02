from benchmarks.bench_knowledge_quality_344 import run


def test_knowledge_quality_receipt_is_bounded_and_excludes_inactive_facts() -> None:
    receipt = run()

    assert receipt["schema"] == "simplicio.fast.knowledge-quality-receipt/v1"
    assert receipt["aggregate"]["precision"] == 1.0
    assert receipt["aggregate"]["recall"] == 1.0
    assert receipt["aggregate"]["ndcg"] == 1.0
    assert receipt["aggregate"]["excluded_inactive_or_conflicted_returned"] == []
    assert receipt["status"] == "partial"
