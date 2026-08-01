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


if __name__ == "__main__":
    unittest.main()
