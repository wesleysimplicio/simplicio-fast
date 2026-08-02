import hashlib

import pytest

from simplicio_fast.knowledge_projection import KnowledgeFact, KnowledgeProjection, KnowledgeProjectionError


def fact(handle: str, text: str, *, state: str = "active") -> KnowledgeFact:
    digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    return KnowledgeFact("adr", "mapper", handle, "v1", ("mapper:fixture:1",), "verified", digest, text, "repo", "tenant", state=state, applicability=("python",))


def test_knowledge_projection_preserves_provenance_and_separates_explain_dimensions() -> None:
    projection = KnowledgeProjection("repo", "tenant", "g1")
    projection.apply_delta([fact("h1", "parser contract and compatibility"), fact("h2", "parser revoked", state="revoked")])
    result = projection.query("parser contract")
    assert result["handles"] == ["h1"]
    assert result["results"][0]["provenance"] == ["mapper:fixture:1"]
    assert set(result["results"][0]["explain"]) >= {"relevance", "trust", "freshness", "applicability"}
    assert result["results"][0]["explain"]["ranking"] == "lexical-fallback"


def test_knowledge_projection_delta_temporal_scope_and_caps_fail_closed() -> None:
    projection = KnowledgeProjection("repo", "tenant", "g1")
    projection.apply_delta([fact("h", "contract",)], tombstones=())
    with pytest.raises(KnowledgeProjectionError, match="fact_scope_mismatch"):
        projection.apply_delta([KnowledgeFact("adr", "mapper", "x", "v1", ("p",), "verified", "sha256:x", "contract", "other", "tenant")])
    assert projection.query("contract", max_bytes=1)["truncated"] is True
    assert projection.query("contract", as_of=100)["handles"] == ["h"]
