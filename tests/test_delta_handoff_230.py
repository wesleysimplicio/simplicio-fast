import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from simplicio_fast.snapshot import build_snapshot
from simplicio_fast.workspace import WorkspaceStore
from simplicio_fast.delta import DeltaError


class ChangedPathDeltaHandoffTest(unittest.TestCase):
    def _store(self, directory: str) -> tuple[Path, WorkspaceStore]:
        root = Path(directory)
        (root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
        (root / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
        return root, WorkspaceStore(root)

    def test_canonical_manifest_and_unchanged_delta_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base(config={"profile": "loop"})
            self.assertEqual(64, len(base.snapshot_sha256))
            self.assertEqual(64, len(base.source_tree_sha256))
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            self.assertEqual({}, delta.changed)
            report = store.handoff(base.generation_id, "issue-230", delta_generation=delta.delta_generation)
            self.assertTrue(report["parity"])
            self.assertEqual(["cold_ms", "incremental_ms", "warm_ms"], sorted(report["timings_ms"]))
            self.assertEqual(2, report["cache_reuse"])

    def test_changed_path_composes_and_matches_full_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text("def one():\n    return 10\n\ndef added():\n    return 3\n", encoding="utf-8")
            full = root / "full.sfast"
            build_snapshot(root, full)
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            with store.compose_delta(base.generation_id, "issue-230", delta.delta_generation) as view:
                self.assertEqual(1, len(view.find("added")))
                self.assertEqual(1, len(view.find("two")))
            report = store.handoff(
                base.generation_id, "issue-230", delta_generation=delta.delta_generation,
                parity_snapshot=full,
            )
            self.assertTrue(report["parity"])
            self.assertEqual(["one.py"], report["changed_paths"])
            self.assertEqual(report["parity_snapshot_hash"], __import__("hashlib").sha256(full.read_bytes()).hexdigest())

    def test_rejects_config_and_artifact_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            with self.assertRaises(DeltaError) as config_error:
                store.create_delta(base.generation_id, "issue-230", config_fingerprint="0" * 64)
            self.assertEqual("config_fingerprint_mismatch", config_error.exception.reason_code)
            snapshot = store.base_dir / base.generation_id / base.snapshot
            snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
            with self.assertRaises(DeltaError) as digest_error:
                store.create_delta(base.generation_id, "issue-230")
            self.assertEqual("base_artifact_digest_mismatch", digest_error.exception.reason_code)

    def test_rejects_stale_source_and_tampered_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text("def one():\n    return 10\n", encoding="utf-8")
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            (root / "one.py").write_text("def one():\n    return 11\n", encoding="utf-8")
            with self.assertRaises(DeltaError) as stale_error:
                store.compose_delta(base.generation_id, "issue-230", delta.delta_generation)
            self.assertEqual("delta_source_stale", stale_error.exception.reason_code)
            path = store.delta_dir / "issue-230" / f"{delta.delta_generation}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["base_commit"] = "wrong"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DeltaError) as digest_error:
                store.delta("issue-230", delta.delta_generation)
            self.assertEqual("delta_digest_mismatch", digest_error.exception.reason_code)

    def test_added_file_preserves_unchanged_base_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text("def one():\n    return 10\n", encoding="utf-8")
            (root / "three.py").write_text("def three():\n    return 3\n", encoding="utf-8")
            full = root / "full.sfast"
            build_snapshot(root, full)
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py", "three.py"])
            report = store.handoff(
                base.generation_id, "issue-230", delta_generation=delta.delta_generation,
                parity_snapshot=full,
            )
            self.assertTrue(report["parity"])
            self.assertEqual(2, report["files_parsed"])
            self.assertEqual(1, report["cache_reuse"])

    def test_rejects_unlisted_current_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text("def one():\n    return 10\n", encoding="utf-8")
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            (root / "two.py").write_text("def two():\n    return 20\n", encoding="utf-8")
            with self.assertRaises(DeltaError) as caught:
                store.compose_delta(base.generation_id, "issue-230", delta.delta_generation)
            self.assertEqual("delta_source_unlisted", caught.exception.reason_code)

    def test_rejects_malformed_records_and_explicit_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            with self.assertRaises(DeltaError) as missing:
                store.create_delta(base.generation_id, "issue-230", ["missing.py"])
            self.assertEqual("delta_path_missing", missing.exception.reason_code)
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            path = store.delta_dir / "issue-230" / f"{delta.delta_generation}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["changed"]["one.py"] = {"sha256": "0" * 64}
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DeltaError) as malformed:
                store.delta("issue-230", delta.delta_generation)
            self.assertEqual("delta_record_invalid", malformed.exception.reason_code)

    def test_rejects_missing_digest_root_and_snapshot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            manifest_path = store.base_dir / base.generation_id / "manifest.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["snapshot_sha256"] = ""
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DeltaError) as missing:
                store.create_delta(base.generation_id, "issue-230")
            self.assertEqual("base_artifact_digest_missing", missing.exception.reason_code)

        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            manifest_path = store.base_dir / base.generation_id / "manifest.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["root"] = str(root / "other")
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DeltaError) as root_error:
                store.create_delta(base.generation_id, "issue-230")
            self.assertEqual("base_root_mismatch", root_error.exception.reason_code)

        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            manifest_path = store.base_dir / base.generation_id / "manifest.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["snapshot"] = "../escape.sfast"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DeltaError) as path_error:
                store.create_delta(base.generation_id, "issue-230")
            self.assertEqual("base_snapshot_path_invalid", path_error.exception.reason_code)

    def test_rejects_dirty_git_canonical_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            with patch("simplicio_fast.workspace._commit", return_value="a" * 40), patch("simplicio_fast.workspace._git_status", return_value=" M one.py"):
                with self.assertRaisesRegex(ValueError, "canonical_base_dirty"):
                    store.build_base()


if __name__ == "__main__":
    unittest.main()
