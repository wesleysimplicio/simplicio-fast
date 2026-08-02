"""Provider-neutral bounded compiler for Code, Knowledge and Operations facts."""

from __future__ import annotations

from typing import Any, Sequence

from .projection import ProjectionEnvelope


CONTEXT_SCHEMA = "simplicio.fast.universal-context/v1"


class UniversalContextError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _tokens(value: Any) -> int:
    return len(str(value).split())


def compile_context(
    projections: Sequence[ProjectionEnvelope],
    *,
    repository_scope: str | None = None,
    max_bytes: int = 256 * 1024,
    max_tokens: int = 4096,
    max_items: int = 128,
) -> dict[str, Any]:
    """Compile a deterministic context packet; inputs remain immutable."""
    if max_bytes <= 0 or max_tokens <= 0 or max_items <= 0:
        raise UniversalContextError("context_budget_invalid")
    selected: list[dict[str, Any]] = []
    reasons: list[str] = []
    token_total = 0
    byte_total = 0
    ordered = sorted(projections, key=lambda item: (item.projection_type, item.stable_handle))
    for envelope in ordered:
        if repository_scope is not None and envelope.payload.get("repository") not in {None, repository_scope}:
            raise UniversalContextError("context_scope_mismatch")
        item = {
            "projection_type": envelope.projection_type,
            "stable_handle": envelope.stable_handle,
            "generation": envelope.generation,
            "producer": envelope.producer,
            "payload": dict(envelope.payload),
            "trust": "derived_fact",
            "authority": "producer",
        }
        encoded_size = len(str(item).encode("utf-8"))
        estimated_tokens = _tokens(item)
        if len(selected) >= max_items:
            reasons.append("item_budget")
            break
        if byte_total + encoded_size > max_bytes:
            reasons.append("byte_budget")
            break
        if token_total + estimated_tokens > max_tokens:
            reasons.append("token_budget")
            break
        selected.append(item)
        byte_total += encoded_size
        token_total += estimated_tokens
    return {
        "schema": CONTEXT_SCHEMA,
        "repository_scope": repository_scope,
        "projections": selected,
        "projection_count": len(selected),
        "bytes": byte_total,
        "estimated_tokens": token_total,
        "truncated": bool(reasons),
        "truncation_reasons": sorted(set(reasons)),
        "authority": "facts_only",
        "instructions": False,
    }


__all__ = ["CONTEXT_SCHEMA", "UniversalContextError", "compile_context"]
