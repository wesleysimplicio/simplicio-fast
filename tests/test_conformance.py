from __future__ import annotations

import unittest

from scripts.conformance import normalize


class ConformanceHarnessTest(unittest.TestCase):
    def test_normalize_maps_python_and_rust_stats_to_one_contract(self) -> None:
        python = {
            "version": 2,
            "bytes": 10,
            "files": 1,
            "symbols": 2,
            "relations": 3,
            "sections": ["symbols", "files"],
            "generation": "SFAST001:abc",
        }
        rust = {**python, "format_version": 2, "sections": ["files", "symbols"]}
        self.assertEqual(normalize(python), normalize(rust))

    def test_normalize_preserves_drift_for_the_gate(self) -> None:
        python = {"version": 2, "bytes": 10, "files": 1, "symbols": 2, "relations": 3, "sections": [], "generation": "a"}
        rust = {"format_version": 2, "bytes": 11, "files": 1, "symbols": 2, "relations": 3, "sections": [], "generation": "a"}
        self.assertNotEqual(normalize(python), normalize(rust))
