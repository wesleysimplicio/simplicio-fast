from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from simplicio_fast.knowledge import (
    KNOWLEDGE_MATERIALIZATION_SCHEMA,
    KNOWLEDGE_RESOLUTION_SCHEMA,
    KnowledgeFacade,
)
from simplicio_fast.skills import AuthorizedSkill, SkillCatalog


def skill(name: str, content: str, *, scope: str = "repo-a") -> AuthorizedSkill:
    return AuthorizedSkill(
        name=name,
        version="1.0.0",
        origin=f"host://skills/{name}",
        description=f"{name} workflow",
        content=content,
        triggers=(name,),
        capabilities=(name,),
        scope=scope,
    )


class KnowledgeFacadeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        catalog = SkillCatalog(Path(self.directory.name), "generation-1", scope="repo-a")
        self.knowledge = KnowledgeFacade(catalog)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_resolve_returns_bounded_skill_t0_and_explicit_unavailable_sources(self) -> None:
        handle = self.knowledge.register(skill("pytest", "SECRET BODY"))

        result = self.knowledge.resolve("run pytest")

        self.assertEqual(KNOWLEDGE_RESOLUTION_SCHEMA, result["schema"])
        self.assertEqual([handle], result["handles"])
        self.assertNotIn("content", result["skills"][0])
        self.assertEqual("available", result["sources"]["skills"]["status"])
        self.assertEqual("unavailable", result["sources"]["memory"]["status"])
        self.assertEqual("unavailable", result["sources"]["context"]["status"])
        self.assertEqual(64, len(result["receipt_digest"]))

    def test_resolve_filters_sources_without_loading_skill_content(self) -> None:
        self.knowledge.register(skill("pytest", "SECRET BODY"))

        result = self.knowledge.resolve("run pytest", sources=("memory", "context"))

        self.assertEqual([], result["skills"])
        self.assertEqual([], result["handles"])
        self.assertEqual({"memory", "context"}, set(result["sources"]))

    def test_expand_handles_preserves_digest_provenance_and_token_budget(self) -> None:
        first = self.knowledge.register(skill("first", "one two"))
        second = self.knowledge.register(skill("second", "three four"))

        result = self.knowledge.expand_handles((first, second), max_entries=2, max_bytes=100, max_tokens=2)

        self.assertEqual(KNOWLEDGE_MATERIALIZATION_SCHEMA, result["schema"])
        self.assertEqual([first], result["references"])
        self.assertEqual(2, result["estimated_tokens"])
        self.assertEqual(hashlib.sha256(b"one two").hexdigest(), result["materialized"][0]["content_sha256"])
        self.assertTrue(result["truncated"])
        self.assertEqual("token_budget_exceeded", result["reason_code"])
        self.assertEqual("repo-a", result["provenance"]["scope"])

    def test_scope_mismatch_and_invalid_budget_fail_closed(self) -> None:
        handle = self.knowledge.register(skill("pytest", "body"))

        with self.assertRaisesRegex(ValueError, "scope"):
            self.knowledge.resolve("pytest", scope="repo-b")
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            self.knowledge.expand_handles((handle,), max_tokens=0)
        with self.assertRaisesRegex(ValueError, "sources"):
            self.knowledge.resolve("pytest", sources=("neural",))


if __name__ == "__main__":
    unittest.main()
