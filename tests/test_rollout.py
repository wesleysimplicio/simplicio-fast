import json
import tempfile
import unittest
from pathlib import Path

from simplicio_fast.rollout import RolloutController


class RolloutTest(unittest.TestCase):
    def test_transitions_are_receipted_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "rollout.json"
            controller = RolloutController(state)
            shadow = controller.transition("shadow", generation="SFAST001:1")
            self.assertEqual("simplicio.fast.rollout-receipt/v1", shadow["schema"])
            canary = controller.transition("canary", generation="SFAST001:2")
            self.assertEqual("shadow", canary["previous_mode"])
            rollback = controller.transition("rollback", reason="validation failed")
            self.assertEqual("rolled-back", rollback["status"])
            self.assertEqual(rollback, json.loads(state.read_text(encoding="utf-8")))
