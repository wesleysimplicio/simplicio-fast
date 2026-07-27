from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from simplicio_fast.ledger import DeliveryLedger
from simplicio_fast.ledger_store import LedgerStore, LedgerStoreError


class LedgerStoreTest(unittest.TestCase):
    def event(self, ledger: DeliveryLedger, event_type: str = "TASK_ACCEPTED"):
        return ledger.append_event(
            event_type,
            task_id="task",
            attempt_id="attempt",
            producer="loop",
            artifact_digests=[hashlib.sha256(b"artifact").hexdigest()],
        )

    def test_hbp_hbi_round_trip_and_chain_verification(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ledger = DeliveryLedger("repo")
            first = self.event(ledger)
            second = self.event(ledger, "TEST_EVIDENCE")
            store = LedgerStore(root)
            first_receipt = store.append(first)
            second_receipt = store.append(second)
            self.assertEqual(0, first_receipt["sequence"])
            self.assertGreater(second_receipt["offset"], first_receipt["offset"])
            self.assertEqual(first.record(), store.read_record(0))
            self.assertEqual(second.record(), store.read_record(1))
            self.assertEqual("valid", store.verify()["status"])

    def test_tampered_body_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ledger = DeliveryLedger("repo")
            store = LedgerStore(root)
            event = self.event(ledger)
            store.append(event)
            body = Path(root, "delivery.hbp").read_bytes()
            body = body[:-1] + bytes([body[-1] ^ 1])
            Path(root, "delivery.hbp").write_bytes(body)
            with self.assertRaises(LedgerStoreError) as error:
                store.read_record(0)
            self.assertEqual("payload_digest_mismatch", error.exception.reason_code)
            self.assertEqual("invalid", store.verify()["status"])

    def test_out_of_bounds_index_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ledger = DeliveryLedger("repo")
            store = LedgerStore(root)
            store.append(self.event(ledger))
            index = bytearray(Path(root, "delivery.hbi").read_bytes())
            offset_position = 13 + 8
            index[offset_position : offset_position + 8] = (10**9).to_bytes(8, "big")
            Path(root, "delivery.hbi").write_bytes(index)
            with self.assertRaises(LedgerStoreError) as error:
                store.read_record(0)
            self.assertEqual("body_out_of_bounds", error.exception.reason_code)
