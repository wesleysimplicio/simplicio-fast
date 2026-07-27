from __future__ import annotations

import hashlib
import threading
import time
import unittest

from simplicio_fast.pager import PageKey, PagerError, SemanticPager


class SemanticPagerTest(unittest.TestCase):
    def key(self, segment: str, generation: str = "g") -> PageKey:
        return PageKey("repo", generation, "slot", segment, "0:10")

    def test_budget_evicts_lru_and_validates_generation(self) -> None:
        pager = SemanticPager("repo", "g", max_bytes=6, max_pages=2)
        pager.get(self.key("a"), lambda: b"aaa")
        pager.get(self.key("b"), lambda: b"bbb")
        pager.get(self.key("a"))
        pager.get(self.key("c"), lambda: b"ccc")
        self.assertEqual(2, pager.stats()["resident_pages"])
        self.assertGreaterEqual(pager.stats()["evictions"], 1)
        with self.assertRaises(PagerError) as error:
            pager.get(self.key("x", "stale"), lambda: b"x")
        self.assertEqual("stale_generation", error.exception.reason_code)

    def test_single_flight_loads_one_page_for_many_readers(self) -> None:
        pager = SemanticPager("repo", "g", max_bytes=100, max_pages=4)
        key = self.key("shared")
        calls = 0
        lock = threading.Lock()

        def loader() -> bytes:
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.03)
            return b"shared"

        results: list[bytes] = []
        threads = [threading.Thread(target=lambda: results.append(pager.get(key, loader))) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, calls)
        self.assertEqual([b"shared"] * 5, results)
        self.assertGreaterEqual(pager.stats()["waits"], 1)

    def test_lease_and_invalidation_preserve_safety(self) -> None:
        data = b"payload"
        pager = SemanticPager("repo", "g", max_bytes=20, max_pages=2)
        key = self.key("held")
        with pager.lease(key, lambda: data, expected_sha256=hashlib.sha256(data).hexdigest()) as leased:
            self.assertEqual(data, leased)
            self.assertEqual({"removed": 0, "held": 1}, pager.invalidate([key]))
            self.assertEqual(1, pager.stats()["held_pages"])
        self.assertEqual(0, pager.stats()["resident_pages"])
        with self.assertRaises(PagerError) as error:
            pager.get(key, lambda: b"wrong", expected_sha256=hashlib.sha256(data).hexdigest())
        self.assertEqual("page_digest_mismatch", error.exception.reason_code)
