"""以临时合成数据验证空答案恢复与紧凑识别，不读取比赛答案。"""
import unittest
from pathlib import Path

from table_qa.application.pipeline import answer_question
from table_qa.application.runner import execute
from table_qa.infrastructure.storage import read_results, save_results
from table_qa.infrastructure.tracing import Trace
from table_qa.schemas import Question, PipelineError
from table_qa.solving.structured import solve_structured
from tests.integration import test_pipeline as fixtures


class CompletionIntegrationTest(unittest.TestCase):
    setUp = fixtures.IntegrationTest.setUp
    tearDown = fixtures.IntegrationTest.tearDown

    def test_empty_direct_and_repair_never_become_success(self):
        class Empty(fixtures.FakeModel):
            def ask(self, stage, payload, images=None, vision=True, fresh=False):
                if stage in {"direct", "repair"}:
                    self.calls.append((stage, fresh))
                    return {"answer": " \n", "evidence": [{"page": 1, "bbox": [0, 0, 200, 100]}]}
                raise AssertionError("无合法候选时不应复核")
        trace = Trace(self.root / "trace")
        pages = [{"page": 1, "width": 200, "height": 100, "image": str(self.root / "files/sample.png")}]
        self.cfg["run"].update(strategy="direct", max_repairs=1)
        q = Question("synthetic", "sample.png", "extract", "最左侧表头", answer_format="string")
        result = answer_question(q, pages, None, Empty(self.cfg, trace, self.root), self.cfg, trace, self.root)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["answer"])
        self.assertEqual([s for s, _ in fixtures.FakeModel.calls], ["direct", "repair"])

    def test_blank_structured_scalar_rejected_but_table_cell_valid(self):
        class Blank(fixtures.FakeModel):
            def ask(self, stage, payload, images=None, vision=True, fresh=False):
                return {"values": [{"ref": "t:r0:c0", "text": ""}]}
        cell = {"id": "t:r0:c0", "text": "", "row": 0, "col": 0, "rowspan": 1, "colspan": 1,
                "sources": [{"page": 1, "bbox": [0, 0, 200, 100]}]}
        table = {"table_id": "t", "row_count": 1, "col_count": 1, "cells": [cell]}
        q = Question("synthetic", "sample.png", "extract", "表头", answer_format="string")
        with self.assertRaisesRegex(PipelineError, "空"):
            solve_structured(q, [table], Blank(self.cfg, Trace(self.root / "trace"), self.root))

    def test_resume_retries_historical_success_with_blank_answer(self):
        result = execute(self.cfg, ids=["1"], model_factory=fixtures.FakeModel)
        folder = Path(self.cfg["paths"]["outputs"]) / result["run_id"]
        rows = read_results(folder)
        rows["1"]["answer"] = ""
        save_results(folder, rows)  # 仅构造临时测试历史，绝不修改真实 outputs。
        before = len(fixtures.FakeModel.calls)
        execute(self.cfg, "resume", base=result["run_id"], model_factory=fixtures.FakeModel)
        self.assertGreater(len(fixtures.FakeModel.calls), before)
        self.assertEqual(read_results(folder)["1"]["answer"], "10")

    def test_compact_recognition_keeps_internal_object_schema(self):
        class Compact(fixtures.FakeModel):
            def ask(self, stage, payload, images=None, vision=True, fresh=False):
                raw = super().ask(stage, payload, images, vision, fresh)
                if stage == "recognize":
                    if payload["cell_format"] != "array":
                        raise AssertionError("未传入紧凑格式")
                    raw["cells"] = [[c[k] for k in ("text", "row", "col", "rowspan", "colspan")] for c in raw["cells"]]
                return raw
        result = execute(self.cfg, ids=["1"], model_factory=Compact)
        rows = read_results(Path(self.cfg["paths"]["outputs"]) / result["run_id"])
        self.assertEqual(rows["1"]["answer"], "10")
        self.assertEqual(rows["1"]["status"], "success")
