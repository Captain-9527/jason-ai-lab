"""用合成图验证失败降级；不调用真实模型。"""
import unittest

from tests.integration import test_pipeline as fixtures
from table_qa.processing.table import recognize_tables
from table_qa.infrastructure.tracing import Trace
from table_qa.schemas import BudgetExceeded, PipelineError


class PartialTableTest(unittest.TestCase):
    setUp = fixtures.IntegrationTest.setUp
    tearDown = fixtures.IntegrationTest.tearDown

    def pages(self, n=2):
        return [{"page": i, "width": 200, "height": 100, "image": str(self.root / "files/sample.png")}
                for i in range(1, n + 1)]

    def test_cross_page_merge_failure_keeps_both_tables(self):
        class BadBoundary(fixtures.FakeModel):
            def ask(self, stage, payload, *args, **kwargs):
                if stage == "boundary":
                    return {"same_table": True, "column_order": [0, 0]}
                return super().ask(stage, payload, *args, **kwargs)
        model = BadBoundary(self.cfg, Trace(self.root / "trace"), self.root)
        tables = recognize_tables(self.pages(), model, self.cfg, self.root)
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(t["incomplete"] for t in tables))
        ids = [c["id"] for t in tables for c in t["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(tables[1]["segments"][0]["page"], 2)

    def test_block_merge_failure_preserves_overlapping_fragments(self):
        class BadBlocks(fixtures.FakeModel):
            def ask(self, stage, payload, *args, **kwargs):
                if stage == "blocks":
                    return {"blocks": [[0, 0, 200, 60], [0, 40, 200, 100]]}
                if stage == "boundary":
                    return {"same_table": False}
                return super().ask(stage, payload, *args, **kwargs)
        self.cfg["processing"]["block_height"] = 70
        tables = recognize_tables(self.pages(1), BadBlocks(self.cfg, Trace(self.root / "trace"), self.root), self.cfg, self.root)
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(t["incomplete"] for t in tables))
        self.assertEqual(tables[1]["segments"][0]["bbox"], [0, 40, 200, 100])

    def test_one_recognition_failure_does_not_discard_other_page(self):
        class Failure(fixtures.FakeModel):
            def ask(self, stage, payload, *args, **kwargs):
                if stage == "recognize" and payload["source"]["page"] == 2:
                    raise PipelineError("模拟输出非法")
                return super().ask(stage, payload, *args, **kwargs)
        tables = recognize_tables(self.pages(), Failure(self.cfg, Trace(self.root / "trace"), self.root), self.cfg, self.root)
        self.assertEqual(len(tables), 1)
        self.assertTrue(tables[0]["incomplete"])

    def test_budget_is_not_swallowed_as_partial_data(self):
        class Limited(fixtures.FakeModel):
            def ask(self, stage, payload, *args, **kwargs):
                if stage == "boundary":
                    raise BudgetExceeded("模拟预算耗尽")
                return super().ask(stage, payload, *args, **kwargs)
        with self.assertRaises(BudgetExceeded):
            recognize_tables(self.pages(), Limited(self.cfg, Trace(self.root / "trace"), self.root), self.cfg, self.root)
