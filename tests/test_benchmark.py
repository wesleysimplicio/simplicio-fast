import sys
import unittest

from benchmarks.run import peak_rss_kib


class BenchmarkPlatformTest(unittest.TestCase):
    def test_peak_rss_is_observable_or_explicitly_unavailable(self) -> None:
        value = peak_rss_kib()
        self.assertTrue(value is None or isinstance(value, int))
        if sys.platform == "win32":
            self.assertIsNotNone(value)
            self.assertGreater(value, 0)


if __name__ == "__main__":
    unittest.main()
