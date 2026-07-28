from __future__ import annotations

import pytest

from simplicio_fast.litert_embeddings import LiteRTEmbeddingError, embed, rerank


def test_deterministic_embed_and_rerank():
    a = embed("UserService authenticate token")
    b = embed("UserService authenticate token")
    assert a == b
    assert a["backend"] == "deterministic-hash"
    ranked = rerank(
        "authenticate token",
        [("a", "UserService authenticate token"), ("b", "filesystem path helper")],
        top_k=2,
    )
    assert ranked[0]["id"] == "a"


def test_litert_mode_fail_closed():
    with pytest.raises(LiteRTEmbeddingError, match="litert_backend_unavailable"):
        embed("x", enable_litert=True)
