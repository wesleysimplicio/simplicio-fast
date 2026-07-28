from __future__ import annotations

import pytest

from simplicio_fast.prism_context_views import (
    build_context_view,
    validate_context_view,
    ContextViewError,
)


def test_context_view_deterministic_and_content_addressed():
    spans = [
        {"path": "b.py", "start": 1, "end": 2, "text": "bbb", "tokens": 10},
        {"path": "a.py", "start": 1, "end": 3, "text": "aaa", "tokens": 10},
    ]
    first = build_context_view(
        agent_id="agent-1",
        stage_id="execute",
        task_id="T1",
        generation_id="g1",
        spans=spans,
        budget_tokens=15,
    )
    second = build_context_view(
        agent_id="agent-1",
        stage_id="execute",
        task_id="T1",
        generation_id="g1",
        spans=list(reversed(spans)),
        budget_tokens=15,
    )
    assert first == second
    assert first["truncated"] is True
    assert validate_context_view(first)["view_hash"] == first["view_hash"]


def test_tamper_rejected():
    view = build_context_view(
        agent_id="a",
        stage_id="s",
        task_id="t",
        generation_id="g",
        spans=[{"path": "a.py", "start": 1, "end": 1, "text": "x", "tokens": 1}],
    )
    view["used_tokens"] = 999
    with pytest.raises(ContextViewError, match="tampered"):
        validate_context_view(view)
