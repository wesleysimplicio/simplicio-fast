"""On-device embedding/rerank surface with deterministic fallback (#186)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

SCHEMA = "simplicio.fast.litert-embeddings/v1"
_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")


class LiteRTEmbeddingError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _tokens(text: str) -> list[str]:
    return sorted({t.casefold() for t in _TOKEN.findall(text or "")})


def deterministic_embed(text: str, *, dims: int = 32) -> list[float]:
    """Hash bag-of-tokens embedding — no neural runtime required."""
    vec = [0.0] * dims
    tokens = _tokens(text)
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(dims):
            vec[i] += ((digest[i % len(digest)] / 255.0) * 2.0) - 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 8) for v in vec]


def embed(text: str, *, enable_litert: bool = False, dims: int = 32) -> dict:
    if enable_litert:
        # Fail closed until Runtime InferenceBackend is bound.
        raise LiteRTEmbeddingError(
            "litert_backend_unavailable",
            "InferenceBackend/v1 not bound; use enable_litert=False for deterministic fallback",
        )
    vector = deterministic_embed(text, dims=dims)
    return {
        "schema": SCHEMA,
        "backend": "deterministic-hash",
        "dims": dims,
        "vector": vector,
        "fact": False,
        "inferred": True,
        "fallback": True,
        "reason_code": None,
    }


def rerank(query: str, documents: Sequence[tuple[str, str]], *, top_k: int = 5) -> list[dict]:
    q = deterministic_embed(query)
    scored = []
    for doc_id, text in documents:
        v = deterministic_embed(text)
        score = sum(a * b for a, b in zip(q, v))
        scored.append({"id": doc_id, "score": round(score, 6), "method": "deterministic_cosine"})
    scored.sort(key=lambda row: (-row["score"], row["id"]))
    return scored[: max(1, top_k)]
