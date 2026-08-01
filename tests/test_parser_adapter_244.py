import copy
from pathlib import Path
import tempfile
import unittest

from simplicio_fast.parser_adapter import (
    ParserAdapterError,
    build_payload,
    validate_payload,
)


class ParserAdapter244Test(unittest.TestCase):
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
