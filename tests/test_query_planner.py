from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simplicio_fast.query_planner import plan_query
from simplicio_fast.snapshot import Snapshot, build_snapshot


class QueryPlannerTest(unittest.TestCase):
    def snapshot(self) -> tuple[tempfile.TemporaryDirectory[str], Snapshot]:
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        (root / "service.py").write_text(
            "class Service:\n    def run(self):\n        return True\n",
            encoding="utf-8",
        )
        path = root / "project.sfast"
        build_snapshot(root, path)
        return holder, Snapshot(path)

    def test_exact_plan_uses_direct_index_and_budget(self) -> None:
        holder, snapshot = self.snapshot()
        try:
            plan = plan_query(snapshot, "Service.run", operation="context", max_results=2, max_bytes=100)
            self.assertEqual("simplicio.fast.query-plan/v1", plan.schema)
            self.assertEqual("exact", plan.selected_index)
            self.assertEqual(1, plan.candidate_records)
            self.assertEqual(72, plan.estimated_bytes)
            self.assertEqual("SFAST001:" + snapshot.sha256, plan.generation)
        finally:
            snapshot.close()
            holder.cleanup()

    def test_filters_are_composed_without_source_materialization(self) -> None:
        holder, snapshot = self.snapshot()
        try:
            plan = plan_query(snapshot, "run", path="service.py", kind="function")
            self.assertEqual("name-substring+path+kind", plan.selected_index)
            self.assertEqual(1, plan.candidate_records)
            self.assertEqual("bounded_name_indexes", plan.reason)
        finally:
            snapshot.close()
            holder.cleanup()

    def test_impact_plan_is_explicitly_relation_bounded(self) -> None:
        holder, snapshot = self.snapshot()
        try:
            plan = plan_query(snapshot, "Service", operation="impact")
            self.assertEqual("relation-scan", plan.selected_index)
            self.assertEqual(snapshot.relation_count, plan.candidate_records)
            self.assertEqual("impact_requires_typed_relation_filter", plan.reason)
        finally:
            snapshot.close()
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
