from simplicio_fast.federation import FederatedEdge, FederationMember, compile_federation
from simplicio_fast.knowledge_projection import KnowledgeFact, KnowledgeProjection
from simplicio_fast.operations_projection import OperationReceipt, OperationsProjection
from simplicio_fast.projection import ProjectionEnvelope
from simplicio_fast.semantic_diff import diff_generations
from simplicio_fast.universal_context import compile_context


def test_synthetic_cross_domain_e2e_is_deterministic_and_handle_only() -> None:
    code = ProjectionEnvelope.create("code", producer="mapper", producer_schema="mapper/v1", generation="g1", stable_handle="code:symbol", payload={"repository": "repo-a", "name": "Symbol"})
    knowledge_index = KnowledgeProjection("repo-a", "tenant-a", "g1")
    knowledge_index.apply_delta([KnowledgeFact("adr", "mapper", "knowledge:adr", "v1", ("fixture:adr",), "verified", "sha256:adr", "Symbol contract", "repo-a", "tenant-a")])
    knowledge_result = knowledge_index.query("Symbol")
    knowledge = ProjectionEnvelope.create("knowledge", producer="knowledge", producer_schema="knowledge/v1", generation="g1", stable_handle="knowledge:adr", payload={"repository": "repo-a", "handles": knowledge_result["handles"]})
    operations = ProjectionEnvelope.create("operations", producer="runtime", producer_schema="runtime.receipt/v1", generation="g1", stable_handle="operations:attempt", payload={"repository": "repo-a", "status": "done"})
    operation_index = OperationsProjection("repo-a", "g1")
    operation_index.ingest([OperationReceipt("operations:attempt", "attempt", "done", "g1", 1, "runtime.receipt/v1", {})])
    federation = compile_federation(
        [FederationMember("repo-a", "commit-a", "g1", "projection/v1", "sha256:a"), FederationMember("repo-b", "commit-b", "g1", "projection/v1", "sha256:b")],
        [FederatedEdge("repo-a:contract", "repo-b:consumer", "depends", 1.0, ("fixture:e1",))],
    )
    context = compile_context([operations, knowledge, code], repository_scope="repo-a")
    diff = diff_generations({"code:symbol": {"name": "Symbol"}}, {"code:symbol": {"name": "Symbol2"}}, source_generation="g1", proposed_generation="g2")
    assert context["projection_count"] == 3
    assert context["instructions"] is False
    assert federation.generation == federation.manifest()["body"]["generation"]
    assert diff.records[0].kind == "update"
    assert all("offset" not in str(item).lower() for item in context["projections"])
    assert context == compile_context([code, knowledge, operations], repository_scope="repo-a")
