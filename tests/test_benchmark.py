import ctypes
import sys
import types
import unittest
from unittest.mock import patch

from benchmarks.run import peak_rss_kib, peak_rss_metric


class _FakeFunction:
    def __init__(self, result: bool) -> None:
        self.result = result

    def __call__(self, _process, counters, _size) -> bool:
        if self.result:
            # PeakWorkingSetSize follows the two DWORD fields in the Windows
            # PROCESS_MEMORY_COUNTERS layout.  Use the pointer passed by ctypes
            # so this fake exercises the same ABI boundary as the real API.
            base = ctypes.cast(counters, ctypes.POINTER(ctypes.c_byte))
            address = ctypes.addressof(base.contents) + 8
            ctypes.cast(address, ctypes.POINTER(ctypes.c_size_t)).contents.value = 8192
        return self.result


class _FakeWindowsLibraries:
    class kernel32:
        @staticmethod
        def GetCurrentProcess() -> object:
            return object()

    def __init__(self, result: bool) -> None:
        self.psapi = type("Psapi", (), {"GetProcessMemoryInfo": _FakeFunction(result)})()


class BenchmarkResourceTest(unittest.TestCase):
    def test_posix_keeps_resource_getrusage(self) -> None:
        fake_resource = types.SimpleNamespace(
            RUSAGE_SELF=0,
            getrusage=lambda _kind: types.SimpleNamespace(ru_maxrss=2048),
        )
        with patch.dict(sys.modules, {"resource": fake_resource}):
            metric = peak_rss_metric("linux")
            with patch.object(sys, "platform", "linux"):
                numeric_value = peak_rss_kib()
        self.assertIsInstance(metric["value"], int)
        self.assertIsNone(metric["reason"])
        self.assertEqual(metric["value"], numeric_value)

    def test_windows_process_memory_api_returns_kib(self) -> None:
        with patch.object(sys, "platform", "win32"):
            with patch.object(ctypes, "windll", _FakeWindowsLibraries(True), create=True):
                metric = peak_rss_metric()
        self.assertEqual(8, metric["value"])
        self.assertIsNone(metric["reason"])

    def test_windows_failure_is_a_machine_readable_partial_metric(self) -> None:
        with patch.object(sys, "platform", "win32"):
            with patch.object(ctypes, "windll", _FakeWindowsLibraries(False), create=True):
                metric = peak_rss_metric()
        self.assertIsNone(metric["value"])
        self.assertEqual("windows_get_process_memory_info_failed", metric["reason"])


if __name__ == "__main__":
    unittest.main()
