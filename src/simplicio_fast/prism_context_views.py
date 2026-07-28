"""Content-addressed context views per agent, stage and task (#214)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

VIEW_SCHEMA = "simplicio.fast.prism-context-view/v1"


class ContextViewError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _sha(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(data).hexdigest()


def build_context_view(
    *,
    agent_id: str,
    stage_id: str,
    task_id: str,
    generation_id: str,
    spans: Sequence[Mapping[str, Any]],
    budget_tokens: int = 4000,
    source_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not agent_id or not stage_id or not task_id or not generation_id:
        raise ContextViewError("identity_required")
    if budget_tokens < 1:
        raise ContextViewError("budget_invalid")
    ordered = sorted((dict(item) for item in spans), key=lambda row: (row.get("path", ""), row.get("start", 0)))
    # Deterministic truncation by path then start.
    selected: list[dict[str, Any]] = []
    used = 0
    for span in ordered:
        cost = int(span.get("tokens") or max(1, len(str(span.get("text", ""))) // 4))
        if used + cost > budget_tokens:
            continue
        selected.append({
            "path": span.get("path"),
            "start": span.get("start"),
            "end": span.get("end"),
            "text_hash": span.get("text_hash") or _sha(span.get("text", "")),
            "tokens": cost,
        })
        used += cost
    identity = {
        "agent_id": agent_id,
        "stage_id": stage_id,
        "task_id": task_id,
        "generation_id": generation_id,
        "spans": selected,
        "source_hashes": dict(sorted((source_hashes or {}).items())),
        "budget_tokens": budget_tokens,
    }
    view_id = _sha(identity)
    body = {
        "schema": VIEW_SCHEMA,
        "view_id": view_id,
        **identity,
        "truncated": len(selected) < len(ordered),
        "used_tokens": used,
    }
    body["view_hash"] = _sha(body)
    return body


def validate_context_view(view: Mapping[str, Any]) -> dict[str, Any]:
    if view.get("schema") != VIEW_SCHEMA:
        raise ContextViewError("schema_invalid", str(view.get("schema")))
    unsigned = {k: v for k, v in view.items() if k != "view_hash"}
    if view.get("view_hash") != _sha(unsigned):
        raise ContextViewError("tampered")
    return dict(view)
