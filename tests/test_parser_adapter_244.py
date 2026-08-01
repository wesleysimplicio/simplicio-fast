import copy
from pathlib import Path
import tempfile
import unittest

from simplicio_fast.parser_adapter import (
    ParserAdapterError,
    adapter_capability,
    build_payload,
    validate_payload,
)


class ParserAdapter244Test(unittest.TestCase):
    def test_capability_receipt_is_versioned_and_bounded(self) -> None:
        capability = adapter_capability()
        self.assertEqual("simplicio.fast.parser-adapter/v1", capability["schema"])
        self.assertEqual("ready", capability["health"])
        self.assertEqual("contract", capability["completeness"])
        self.assertEqual("1", capability["version"])
        self.assertEqual(64, len(capability["fingerprints"]["contract_sha256"]))
        self.assertIn("integrated", capability["modes"])

    def test_builder_rejects_invalid_mode_limits_and_previous_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserAdapterError, "mode_invalid"):
                build_payload(root, mode="unknown")
            with self.assertRaisesRegex(ParserAdapterError, "limit_invalid"):
                build_payload(root, limits={"max_files": 0})
            with self.assertRaisesRegex(ParserAdapterError, "limit_invalid"):
                build_payload(root, limits={"max_files": True})
            previous = build_payload(root)
            with self.assertRaisesRegex(
                ParserAdapterError, "previous_payload_requires_changed_paths"
            ):
                build_payload(root, previous_payload=previous)

    def test_builder_reports_encoding_and_parse_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_bytes(b"def bad(:\n\xff")
            payload = build_payload(root)
            self.assertEqual("partial", payload["completeness"])
            self.assertEqual("encoding_invalid", payload["diagnostics"][0]["code"])
            (root / "bad.py").write_text("def bad(:\n    pass\n", encoding="utf-8")
            payload = build_payload(root)
            self.assertEqual("parse_failed", payload["diagnostics"][0]["code"])

    def test_validator_rejects_contract_metadata_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
            payload = build_payload(root)
            for field, value, reason in (
                ("schema", "future", "schema_unsupported"),
                ("mode", "future", "mode_invalid"),
                ("producer", "other", "producer_invalid"),
                ("adapter_version", "2", "adapter_version_unsupported"),
                ("commit", "short", "commit_invalid"),
                ("changed_paths", "bad", "changed_paths_invalid"),
                ("completeness", "unknown", "completeness_invalid"),
                ("diagnostics", "bad", "diagnostics_invalid"),
                ("invalidation", "bad", "invalidation_invalid"),
                ("workspace_fingerprints", "bad", "workspace_fingerprints_invalid"),
            ):
                broken = copy.deepcopy(payload)
                broken[field] = value
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ParserAdapterError, reason):
                        validate_payload(broken)

    def test_integrated_mode_requires_mapper_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "def run():\n    return True\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ParserAdapterError, "mapper_required"):
                build_payload(root, mode="integrated")
            payload = build_payload(
                root,
                mode="integrated",
                mapper_generation="mapper-g1",
                commit="a" * 40,
            )
            self.assertEqual("integrated", payload["mode"])

    def test_generated_directories_are_not_ingested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src.py").write_text(
                "def source():\n    return 1\n", encoding="utf-8"
            )
            generated = root / "target"
            generated.mkdir()
            (generated / "generated.rs").write_text(
                "fn generated() {}\n", encoding="utf-8"
            )
            payload = build_payload(root)
            self.assertEqual(["src.py"], [item["path"] for item in payload["files"]])

    def test_python_payload_is_deterministic_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "class Service:\n    def run(self):\n        return True\n",
                encoding="utf-8",
            )
            first = build_payload(root)
            second = build_payload(root)
            self.assertEqual(first, second)
            receipt = validate_payload(first)
            self.assertEqual("valid", receipt["status"])
            self.assertEqual(2, receipt["symbols"])
            self.assertGreaterEqual(receipt["relations"], 2)
            self.assertIn("definition", {item["kind"] for item in first["relations"]})
            self.assertEqual("1", first["adapter_version"])
            self.assertEqual({}, first["workspace_fingerprints"])

    def test_python_relations_preserve_calls_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "def helper():\n    return True\n\ndef test_helper():\n    return helper()\n",
                encoding="utf-8",
            )
            payload = build_payload(root)
            kinds = {item["kind"] for item in payload["relations"]}
            self.assertTrue({"definition", "call", "test"} <= kinds)

    def test_lexical_language_adapters_emit_relations(self) -> None:
        fixtures = {
            "service.ts": 'import { helper } from "./helper";\nexport function testService() { return helper(); }\n',
            "service.rs": "use crate::helper;\nfn test_service() {}\n",
            "service.cs": "using System.Text;\npublic class Service {}\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in fixtures.items():
                (root / name).write_text(source, encoding="utf-8")
            payload = build_payload(root)
            by_language = {
                item["language"]: {
                    relation["kind"]
                    for relation in payload["relations"]
                    if relation["file"] == item["path"]
                }
                for item in payload["files"]
            }
            self.assertIn("import", by_language["typescript"])
            self.assertIn("definition", by_language["rust"])
            self.assertIn("import", by_language["csharp"])
            self.assertEqual(
                {"csharp", "rust", "typescript"},
                set(payload["workspace_fingerprints"]),
            )
            assert payload["completeness"] == "partial"
            assert {
                item["path"]
                for item in payload["diagnostics"]
                if item["code"] == "native_parser_unavailable"
            } == set(fixtures)

    def test_changed_paths_are_scoped_and_invalid_payloads_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
            payload = build_payload(root, changed_paths=["one.py"])
            self.assertEqual(["one.py"], payload["changed_paths"])
            self.assertEqual(["one.py"], [item["path"] for item in payload["files"]])
            broken = copy.deepcopy(payload)
            broken["payload_sha256"] = "0" * 64
            with self.assertRaisesRegex(ParserAdapterError, "payload_digest_mismatch"):
                validate_payload(broken)
            with self.assertRaisesRegex(ParserAdapterError, "path_escape"):
                build_payload(root, changed_paths=["../outside.py"])

    def test_previous_payload_reuses_unchanged_files_and_reports_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
            previous = build_payload(root)
            (root / "one.py").write_text("def one():\n    return 3\n", encoding="utf-8")
            (root / "two.py").unlink()

            current = build_payload(
                root,
                changed_paths=["one.py", "two.py"],
                previous_payload=previous,
            )
            assert [item["path"] for item in current["files"]] == ["one.py"]
            assert current["invalidation"] == {
                "schema": "simplicio.fast.parser-invalidation/v1",
                "requested_paths": ["one.py", "two.py"],
                "parsed_paths": ["one.py"],
                "reused_paths": [],
                "deleted_paths": ["two.py"],
                "reason_codes": ["explicit_changed_paths", "previous_payload_reuse"],
            }

    def test_previous_payload_reuses_unmodified_file_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
            previous = build_payload(root)
            (root / "one.py").write_text("def one():\n    return 3\n", encoding="utf-8")

            current = build_payload(
                root,
                changed_paths=["one.py"],
                previous_payload=previous,
            )
            assert {item["path"] for item in current["files"]} == {"one.py", "two.py"}
            assert current["invalidation"]["parsed_paths"] == ["one.py"]
            assert current["invalidation"]["reused_paths"] == ["two.py"]

    def test_previous_payload_rejects_stale_unmodified_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
            previous = build_payload(root)
            (root / "one.py").write_text("def one():\n    return 3\n", encoding="utf-8")
            (root / "two.py").write_text("def two():\n    return 4\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserAdapterError, "source_digest_mismatch"):
                build_payload(
                    root,
                    changed_paths=["one.py"],
                    previous_payload=previous,
                )

    def test_adapter_limits_fail_closed_before_returning_partial_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text(
                "def one():\n    return 1\n", encoding="utf-8"
            )
            (root / "two.py").write_text(
                "def two():\n    return 2\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ParserAdapterError, "file_limit_exceeded"):
                build_payload(root, limits={"max_files": 1})
            with self.assertRaisesRegex(ParserAdapterError, "symbol_limit_exceeded"):
                build_payload(root, limits={"max_symbols": 1})

    def test_validator_rejects_oversized_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "def run():\n    return True\n", encoding="utf-8"
            )
            payload = build_payload(root)
            payload["symbols"] = payload["symbols"] * 1_000_001
            with self.assertRaisesRegex(ParserAdapterError, "symbol_limit_exceeded"):
                validate_payload(payload)

    def test_validator_rejects_incomplete_integrated_identity_and_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "def run():\n    return True\n", encoding="utf-8"
            )
            payload = build_payload(
                root,
                mode="integrated",
                mapper_generation="mapper-g1",
                commit="a" * 40,
            )
            payload["commit"] = "short"
            with self.assertRaisesRegex(ParserAdapterError, "mapper_identity_invalid"):
                validate_payload(payload)
            payload = build_payload(root)
            payload["symbols"][0]["end_line"] = 0
            with self.assertRaisesRegex(ParserAdapterError, "symbol_invalid"):
                validate_payload(payload)

    def test_validator_rejects_invalid_relation_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "def helper():\n    return True\n\ndef test_helper():\n    return helper()\n",
                encoding="utf-8",
            )
            payload = build_payload(root)
            payload["relations"][0]["confidence"] = 2
            with self.assertRaisesRegex(ParserAdapterError, "relation_file_missing"):
                validate_payload(payload)


if __name__ == "__main__":
    unittest.main()
