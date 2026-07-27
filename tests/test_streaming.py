from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from simplicio_fast.streaming import StreamingBlockStore, StreamingStoreError


class StreamingBlockStoreTest(unittest.TestCase):
    def test_blocked_round_trip_publishes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            payload = b"abcdefghij" * 100
            store = StreamingBlockStore(root)
            receipt = store.build((payload[index : index + 13] for index in range(0, len(payload), 13)), generation="g1", block_bytes=64)
            self.assertEqual("published", receipt["status"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), receipt["source_sha256"])
            self.assertEqual(payload, store.read_all("g1"))

    def test_resume_uses_valid_checkpoint_and_rejects_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = StreamingBlockStore(root)
            with self.assertRaisesRegex(RuntimeError, "injected_stream_failure"):
                store.build((b"a" * 32, b"b" * 32, b"c" * 4), generation="g1", block_bytes=32, fail_after_blocks=1)
            with self.assertRaises(StreamingStoreError) as error:
                store.build((b"x" * 32, b"b" * 32, b"c" * 4), generation="g1", block_bytes=32, resume=True)
            self.assertEqual("resume_source_mismatch", error.exception.reason_code)
            receipt = store.build((b"a" * 32, b"b" * 32, b"c" * 4), generation="g1", block_bytes=32, resume=True)
            self.assertEqual("published", receipt["status"])
            self.assertEqual(b"a" * 32 + b"b" * 32 + b"c" * 4, store.read_all("g1"))

    def test_read_range_is_bounded_and_validates_touched_block(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            payload = bytes(range(64)) * 4
            store = StreamingBlockStore(root)
            store.build((payload,), generation="g1", block_bytes=32)
            self.assertEqual(payload[11:75], store.read_range("g1", 11, 64))
            self.assertEqual(b"", store.read_range("g1", 0, 0))
            with self.assertRaises(StreamingStoreError) as error:
                store.read_range("g1", -1, 1)
            self.assertEqual("invalid_range", error.exception.reason_code)
            segment = Path(root, "g1", "segment-000000.bin")
            data = bytearray(segment.read_bytes())
            data[12] ^= 1
            segment.write_bytes(data)
            with self.assertRaises(StreamingStoreError) as error:
                store.read_range("g1", 0, 16)
            self.assertEqual("block_digest", error.exception.reason_code)
