import tempfile
import unittest
import hashlib
import random
from pathlib import Path

from simplicio_fast.snapshot import (
    LEGACY_FILE_RECORD,
    LEGACY_HEADER,
    LEGACY_SYMBOL_RECORD,
    MAGIC,
    SourceEncodingError,
    Snapshot,
    SnapshotBuildTimeout,
    StaleSnapshotError,
    build_snapshot,
    source_files,
)


class SnapshotTest(unittest.TestCase):
    def test_source_files_prunes_ignored_directories_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def run():\n    return True\n")
            (root / "node_modules" / "nested").mkdir(parents=True)
            (root / "node_modules" / "nested" / "ignored.py").write_text("def ignored():\n    pass\n")
            (root / ".git" / "objects").mkdir(parents=True)
            (root / ".git" / "objects" / "ignored.py").write_text("def ignored():\n    pass\n")

            self.assertEqual([root / "app.py"], source_files(root))

    def test_binary_snapshot_query_and_incremental_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("class User:\n    def save(self):\n        return True\n")
            output = root / ".index/project.sfast"

            cold = build_snapshot(root, output)
            self.assertEqual(1, cold.parsed_files)
            with Snapshot(output) as snapshot:
                matches = snapshot.find("save")
                self.assertEqual("User.save", matches[0].qualified_name)
                context = snapshot.context(root, "save")
                self.assertEqual("User.save", context[0].symbol)
                self.assertIn("def save", context[0].content)
                self.assertEqual(64, len(context[0].source_sha256))
                self.assertEqual(64, len(snapshot.sha256))
                self.assertEqual(f"SFAST001:{snapshot.sha256}", snapshot.generation)

            warm = build_snapshot(root, output)
            self.assertEqual(0, warm.parsed_files)
            self.assertEqual(1, warm.reused_files)

            source.write_text(source.read_text() + "\ndef deactivate():\n    return False\n")
            with Snapshot(output) as snapshot:
                with self.assertRaises(StaleSnapshotError):
                    snapshot.context(root, "save")
            changed = build_snapshot(root, output)
            self.assertEqual(1, changed.parsed_files)
            with Snapshot(output) as snapshot:
                self.assertEqual(1, len(snapshot.find("deactivate")))

    def test_v2_indexes_relations_and_context_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "import helpers\n\nclass Service:\n    def run(self):\n        return helpers.go()\n"
            )
            output = root / "project.sfast"
            metrics = build_snapshot(root, output)
            self.assertEqual(2, metrics.format_version)
            with Snapshot(output) as snapshot:
                self.assertEqual("SFAST001/v2", snapshot.stats()["format"])
                self.assertEqual("Service.run", snapshot.find_exact("Service.run")[0].qualified_name)
                self.assertTrue(any(item.kind == "call" for item in snapshot.impact("go")))
                spans = snapshot.context(root, "run", max_bytes=6, max_tokens=2)
                self.assertLessEqual(sum(len(item.content.encode()) for item in spans), 6)
                self.assertLessEqual(sum(item.tokens for item in spans), 2)

    def test_async_imports_and_repository_derived_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text('[remote "origin"]\n    url = https://example.invalid/repo.git\n')
            (root / "module.py").write_text(
                "from package import helper\nfrom .local import value\n\nasync def load(item):\n    return helper(item)\n"
            )
            output = root / "project.sfast"
            build_snapshot(root, output)
            with Snapshot(output) as snapshot:
                loaded = snapshot.find_exact("load")[0]
                self.assertEqual("async_function", loaded.kind)
                self.assertTrue(loaded.signature)
                self.assertEqual(64, len(loaded.symbol_id))
                self.assertTrue(any(item.kind == "import" for item in snapshot.relations()))

    def test_corruption_and_truncation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("def hello():\n    return True\n")
            output = root / "project.sfast"
            build_snapshot(root, output)
            original = output.read_bytes()
            output.write_bytes(original[:-1])
            with self.assertRaises(ValueError):
                Snapshot(output)

            rng = random.Random(2)
            for _ in range(20):
                mutated = bytearray(original)
                mutated[rng.randrange(len(mutated))] ^= 1 << rng.randrange(8)
                output.write_bytes(mutated)
                with self.assertRaises(ValueError):
                    Snapshot(output)
            output.write_bytes(original)
            corrupted = bytearray(original)
            corrupted[-1] ^= 0xFF
            output.write_bytes(corrupted)
            with self.assertRaises(ValueError):
                Snapshot(output)

    def test_reads_frozen_v1_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            strings = b"sample.pyA"
            files_offset = LEGACY_HEADER.size
            symbols_offset = files_offset + LEGACY_FILE_RECORD.size
            strings_offset = symbols_offset + LEGACY_SYMBOL_RECORD.size
            total_size = strings_offset + len(strings)
            payload = bytearray(total_size)
            LEGACY_HEADER.pack_into(
                payload,
                0,
                MAGIC,
                1,
                1,
                1,
                files_offset,
                symbols_offset,
                strings_offset,
                total_size,
            )
            LEGACY_FILE_RECORD.pack_into(payload, files_offset, 0, 9, 1, 0, hashlib.sha256(b"x").digest())
            LEGACY_SYMBOL_RECORD.pack_into(payload, symbols_offset, 9, 1, 0, 1, 1, 2)
            payload[strings_offset:] = strings
            output = root / "legacy.sfast"
            output.write_bytes(payload)
            with Snapshot(output) as snapshot:
                self.assertEqual(1, snapshot.format_version)
                self.assertEqual("A", snapshot.find_exact("A")[0].name)


    def test_python_encoding_policy_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latin = root / "latin.py"
            latin.write_bytes(b"# coding: latin-1\nclass Caf\xe9:\n    pass\n")
            output = root / "project.sfast"

            build_snapshot(root, output)
            with Snapshot(output) as snapshot:
                self.assertEqual("Café", snapshot.find_exact("Café")[0].name)

            invalid = root / "invalid.py"
            invalid.write_bytes(b"\xff\xfe\x00\x01")
            with self.assertRaises(SourceEncodingError) as raised:
                build_snapshot(root, output)
            self.assertEqual("source_encoding_unreadable", raised.exception.code)
            self.assertIn("invalid.py", str(raised.exception))



    def test_build_recovers_from_truncated_snapshot_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("def recovered():\n    return True\n")
            output = root / "project.sfast"
            output.write_bytes(b"truncated")
            build_snapshot(root, output)
            with Snapshot(output) as snapshot:
                self.assertEqual(1, len(snapshot.find("recovered")))
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_bounded_build_preserves_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("def original():\n    return True\n")
            output = root / "project.sfast"
            build_snapshot(root, output)
            before = output.read_bytes()
            source.write_text("def changed():\n    return True\n")
            with self.assertRaises(SnapshotBuildTimeout):
                build_snapshot(root, output, timeout_seconds=0)
            self.assertEqual(before, output.read_bytes())


    def test_oversized_source_fails_before_parse_and_preserves_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("def original():\n    return True\n")
            output = root / "project.sfast"
            build_snapshot(root, output)
            before = output.read_bytes()
            source.write_text("x" * 32)
            with self.assertRaisesRegex(ValueError, "source_file_too_large"):
                build_snapshot(root, output, max_file_bytes=4)
            self.assertEqual(before, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
