from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from simplicio_fast.catalog import AddressCatalog, CatalogResolutionError, SCHEMA


class AddressCatalogTest(unittest.TestCase):
    def _catalog(self, root: Path, generation: str = "SFAST001:generation") -> AddressCatalog:
        return AddressCatalog(root, generation)

    def test_handles_are_deterministic_and_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = "a" * 64
            source = "b" * 64
            first = self._catalog(root)
            second = self._catalog(root)
            a = first.register("symbol", canonical, b"class User", source_sha256=source)
            b = second.register("symbol", canonical, b"class User", source_sha256=source)
            self.assertEqual(a.handle, b.handle)
            self.assertNotEqual(a.handle, self._catalog(root, "SFAST001:other")._make_handle("symbol", canonical))
            self.assertEqual(SCHEMA, first.stat()["schema"])

    def test_resolution_fails_closed_for_scope_state_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._catalog(root)
            entry = catalog.register("span", "c" * 64, b"context", source_sha256="d" * 64)
            self.assertEqual(b"context", catalog.resolve(entry.handle, generation=catalog.generation).payload)
            for kwargs, reason in (
                ({"generation": "SFAST001:stale"}, "stale_generation"),
                ({"repository": root / "other"}, "cross_repo_handle"),
                ({"payload_sha256": "e" * 64}, "payload_digest_mismatch"),
            ):
                with self.assertRaises(CatalogResolutionError) as error:
                    catalog.resolve(entry.handle, **kwargs)
                self.assertEqual(reason, error.exception.reason_code)
            catalog.tombstone(entry.handle)
            with self.assertRaises(CatalogResolutionError) as error:
                catalog.resolve(entry.handle)
            self.assertEqual("tombstoned", error.exception.reason_code)

    def test_binary_round_trip_verifies_and_detects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._catalog(root)
            catalog.register("file", "f" * 64, b"hello", source_sha256=hashlib.sha256(b"source").hexdigest())
            catalog.register("test", "1" * 64, b"pytest", source_sha256="2" * 64, state="held")
            path = root / "catalog.sfc"
            receipt = catalog.save(path)
            self.assertEqual("valid", receipt["status"])
            loaded = AddressCatalog.load(path)
            self.assertEqual(catalog.stat(), loaded.stat())
            self.assertEqual("valid", loaded.verify()["status"])
            corrupted = bytearray(path.read_bytes())
            corrupted[-1] ^= 1
            with self.assertRaises(CatalogResolutionError):
                AddressCatalog.from_bytes(bytes(corrupted))
