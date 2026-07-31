import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.changed_path_delta_230 import run as run_delta_benchmark
from simplicio_fast.delta import DeltaError
from simplicio_fast.snapshot import build_snapshot
from simplicio_fast.workspace import WorkspaceStore


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
            self.assertGreater(report["mapped_bytes"], 0)
            self.assertGreaterEqual(report["cpu_ms"], 0)

    def test_repeated_identical_delta_is_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            first = store.create_delta(base.generation_id, "same", ["one.py"])
            path = store.delta_dir / "same" / f"{first.delta_generation}.json"
            first_bytes = path.read_bytes()
            second = store.create_delta(base.generation_id, "same", ["one.py"])
            self.assertEqual(first.delta_generation, second.delta_generation)
            self.assertEqual(first_bytes, path.read_bytes())

    def test_changed_path_composes_and_matches_full_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text(
                "def one():\n    return 10\n\ndef added():\n    return 3\n",
                encoding="utf-8",
            )
            full = root / "full.sfast"
            build_snapshot(root, full)
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            with store.compose_delta(base.generation_id, "issue-230", delta.delta_generation) as view:
                self.assertEqual(1, len(view.find("added")))
                self.assertEqual(1, len(view.find("two")))
            report = store.handoff(
                base.generation_id,
                "issue-230",
                delta_generation=delta.delta_generation,
                parity_snapshot=full,
            )
            self.assertTrue(report["parity"])
            self.assertEqual(["one.py"], report["changed_paths"])
            self.assertEqual(
                report["parity_snapshot_hash"],
                hashlib.sha256(full.read_bytes()).hexdigest(),
            )

    def test_tombstone_rollback_keeps_source_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "two.py").unlink()
            full = root / "after-delete.sfast"
            build_snapshot(root, full)
            delta = store.create_delta(base.generation_id, "delete", ["two.py"])
            self.assertTrue(delta.changed["two.py"]["tombstone"])
            with store.compose_delta(base.generation_id, "delete", delta.delta_generation) as view:
                self.assertEqual([], view.find("two"))
            report = store.handoff(
                base.generation_id,
                "delete",
                delta_generation=delta.delta_generation,
                parity_snapshot=full,
            )
            self.assertTrue(report["parity"])
            delta_path = store.delta_dir / "delete" / f"{delta.delta_generation}.json"
            delta_path.unlink()
            self.assertFalse((root / "two.py").exists())
            rebuilt = root / "rollback-rebuild.sfast"
            build_snapshot(root, rebuilt)
            self.assertTrue(rebuilt.is_file())

    def test_twenty_concurrent_readers_share_one_immutable_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()

            def read_base(_: int) -> tuple[int, str]:
                with store.open(base.generation_id) as view:
                    symbols = view.find("one")
                    return len(symbols), symbols[0].base_generation

            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(read_base, range(20)))
            self.assertEqual([(1, base.generation_id)] * 20, results)

    def test_worktree_delta_records_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text("def one_a():\n    return 10\n", encoding="utf-8")
            delta_a = store.create_delta(base.generation_id, "slot-a", ["one.py"])
            with store.compose_delta(base.generation_id, "slot-a", delta_a.delta_generation) as view:
                self.assertEqual(1, len(view.find("one_a")))
            (root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def two_b():\n    return 20\n", encoding="utf-8")
            delta_b = store.create_delta(base.generation_id, "slot-b", ["two.py"])
            self.assertEqual(["one.py"], sorted(delta_a.changed))
            self.assertEqual(["two.py"], sorted(delta_b.changed))
            self.assertNotEqual(delta_a.delta_generation, delta_b.delta_generation)
            self.assertTrue((store.delta_dir / "slot-a").is_dir())
            self.assertTrue((store.delta_dir / "slot-b").is_dir())
            self.assertEqual(["one.py"], sorted(store.delta("slot-a", delta_a.delta_generation).changed))

    def test_active_lease_protects_generation_from_gc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base(config={"profile": "one"})
            other = store.build_base(config={"profile": "two"})
            lease = store.pin(base.generation_id, "reader-20", ttl_seconds=60)
            dry = store.gc()
            self.assertIn(base.generation_id, dry["protected"])
            self.assertIn(other.generation_id, dry["candidates"])
            applied = store.gc(apply=True)
            self.assertNotIn(base.generation_id, applied["removed"])
            self.assertFalse(store._manifest_path(other.generation_id).exists())
            store.release_lease(lease.lease_id)
            released = store.gc(apply=True)
            self.assertIn(base.generation_id, released["removed"])
            self.assertTrue((root / "one.py").exists())

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

    def test_rejects_truncated_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            delta = store.create_delta(base.generation_id, "issue-230", ["one.py"])
            path = store.delta_dir / "issue-230" / f"{delta.delta_generation}.json"
            path.write_bytes(b"{")
            with self.assertRaises(DeltaError) as caught:
                store.delta("issue-230", delta.delta_generation)
            self.assertEqual("delta_missing", caught.exception.reason_code)

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

    def test_cli_delta_and_handoff_are_real_system_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            base = store.build_base()
            (root / "one.py").write_text("def one_cli():\n    return 30\n", encoding="utf-8")
            parity = root / "cli-parity.sfast"
            build_snapshot(root, parity)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
            delta_command = [
                sys.executable,
                "-m",
                "simplicio_fast.cli",
                "delta",
                str(root),
                "--base-generation",
                base.generation_id,
                "--worktree-id",
                "cli",
                "--changed-path",
                "one.py",
            ]
            delta_result = subprocess.run(
                delta_command,
                capture_output=True,
                text=True,
                env=environment,
                check=True,
            )
            delta_payload = json.loads(delta_result.stdout)
            generation = delta_payload["delta"]["delta_generation"]
            handoff_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "simplicio_fast.cli",
                    "handoff",
                    str(root),
                    "--base-generation",
                    base.generation_id,
                    "--worktree-id",
                    "cli",
                    "--delta-generation",
                    generation,
                    "--parity-snapshot",
                    str(parity),
                ],
                capture_output=True,
                text=True,
                env=environment,
                check=True,
            )
            report = json.loads(handoff_result.stdout)
            self.assertEqual("simplicio.fast.handoff/v1", report["schema"])
            self.assertEqual("pass", report["status"])
            self.assertEqual(1, report["files_parsed"])
            self.assertEqual(1, report["cache_reuse"])
            self.assertGreater(report["mapped_bytes"], 0)
            self.assertGreaterEqual(report["cpu_ms"], 0)

    def test_benchmark_receipt_has_required_matrix_and_metrics(self) -> None:
        receipt = run_delta_benchmark(files=4, repetitions=10)
        self.assertEqual("pass", receipt["status"])
        self.assertEqual(10, receipt["workload"]["repetitions"])
        self.assertEqual(["cold", "warm", "unchanged", "one_file"], receipt["workload"]["categories"])
        for category in receipt["workload"]["categories"]:
            summary = receipt["categories"][category]["summary"]
            self.assertEqual(10, summary["repetitions"])
            self.assertTrue(summary["parity"])
            for field in (
                "wall_ms_median",
                "cpu_ms_median",
                "rss_kib_median",
                "page_faults_median",
                "parsed_files_median",
                "reused_files_median",
                "parsed_bytes_median",
                "reused_bytes_median",
                "mapped_bytes_median",
            ):
                self.assertIn(field, summary)
        self.assertEqual("complete", receipt["environment"]["metrics_status"])

    def test_benchmark_rejects_fewer_than_ten_repetitions(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 10"):
            run_delta_benchmark(files=4, repetitions=9)

    def test_rejects_dirty_git_canonical_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, store = self._store(directory)
            with patch(
                "simplicio_fast.workspace._commit",
                return_value="a" * 40,
            ), patch(
                "simplicio_fast.workspace._git_status",
                return_value=" M one.py",
            ):
                with self.assertRaisesRegex(ValueError, "canonical_base_dirty"):
                    store.build_base()


if __name__ == "__main__":
    unittest.main()