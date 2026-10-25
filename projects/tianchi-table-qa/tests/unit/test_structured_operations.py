"""用合成表格覆盖数量原文提取、派生计数与操作协议边界。"""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from table_qa.application.pipeline import answer_question
from table_qa.config import load_config
from table_qa.infrastructure.tracing import Trace
from table_qa.schemas import Question, PipelineError
from table_qa.solving.direct import solve_direct
from table_qa.solving.structured import solve_structured


class ResponseModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def ask(self, stage, payload, *args, **kwargs):
        self.calls.append((stage, payload))
        if stage == "review":
            return {"verdict": "pass", "confidence": .95, "risks": [], "preferred": "structured"}
        if stage == "direct":
            return {"answer": "2", "evidence": [{"page": 1, "bbox": [0, 0, 100, 100]}]}
        return self.response


class StructuredOperationsTest(unittest.TestCase):
    def setUp(self):
        self.q = Question("synthetic", "synthetic.png", "extract", "提取分类数量对应的值", answer_format="number", route="lookup")
        source = {"page": 1, "bbox": [0, 0, 100, 100]}
        self.tables = [{"table_id": "t", "row_count": 4, "col_count": 2, "segments": [source], "cells": [
            {"id": key, "text": text, "row": row, "col": col, "rowspan": span, "colspan": 1, "sources": [source]}
            for key, text, row, col, span in [("h", "分类", 0, 0, 1), ("a", "甲类", 1, 0, 2),
                                              ("b", "乙类", 3, 0, 1), ("n", "12.00", 0, 1, 1)]]}]
        self.pages = [{"page": 1, "width": 100, "height": 100, "image": "unused.png"}]

    def test_predicate_filters_full_numeric_domain_in_program(self):
        q = replace(self.q, question="大于1.5的数量", route="filter")
        cells = [dict(self.tables[0]["cells"][-1], id=key, text=text, row=row)
                 for row, (key, text) in enumerate((("v1", "1.5"), ("v2", "1.6～2.2"), ("v3", "0.5～2.0")))]
        tables = [{**self.tables[0], "cells": cells}]
        raw = {"operation": "calculate", "plan": {"op": "count", "refs": ["v1", "v2", "v3"],
               "predicate": {"operator": "gt", "value": "1.5", "range_policy": "all"}}}
        result = solve_structured(q, tables, ResponseModel(raw))
        self.assertEqual(result["answer"], "1")
        self.assertEqual(len(result["source_cells"]), 3)
        raw["plan"]["predicate"]["range_policy"] = "any"
        self.assertEqual(solve_structured(q, tables, ResponseModel(raw))["answer"], "2")

    def test_declared_domain_rejects_missing_candidate_cells(self):
        raw = {"operation": "calculate", "plan": {"op": "count", "refs": ["a"],
               "domain": {"table_id": "t", "column": 0, "start_row": 1, "end_row": 4}}}
        with self.assertRaisesRegex(PipelineError, "完整覆盖"):
            solve_structured(self.q, self.tables, ResponseModel(raw))
        raw["plan"]["refs"] = ["a", "b"]
        self.assertEqual(solve_structured(self.q, self.tables, ResponseModel(raw))["answer"], "2")

    def test_tied_extrema_can_be_explicitly_joined_for_string_contract(self):
        q = replace(self.q, answer_format="string", route="filter")
        raw = {"operation": "calculate", "plan": {"op": "argmax", "refs": ["n", "n"],
               "labels": ["a", "b"], "separator": ", "}}
        self.assertEqual(solve_structured(q, self.tables, ResponseModel(raw))["answer"], "甲类, 乙类")

    def test_predicate_rejects_ambiguous_ranges_and_missing_values(self):
        from table_qa.solving.calculator import calculate
        for values, predicate in [(["1～2"], {"operator": "gt", "value": "1.5"}),
                                   (["--"], {"operator": "gt", "value": "1.5"}),
                                   (["2"], {"operator": "exec", "value": "1.5"})]:
            with self.subTest(values=values), self.assertRaises(PipelineError):
                calculate({"op": "count", "predicate": predicate}, values)

    def test_predicate_boundaries_missing_and_empty_domain(self):
        from table_qa.solving.calculator import calculate
        for operator, policy, expected in [("gt", "all", "0"), ("ge", "all", "1"),
                                            ("lt", "any", "0"), ("le", "any", "1"),
                                            ("eq", "any", "1"), ("eq", "all", "0"),
                                            ("ne", "any", "1"), ("ne", "all", "0")]:
            plan = {"op": "count", "predicate": {"operator": operator, "value": "1", "range_policy": policy}}
            with self.subTest(operator=operator, policy=policy):
                self.assertEqual(calculate(plan, ["1～2"]), expected)
        plan = {"op": "count", "predicate": {"operator": "gt", "value": "1", "missing": "exclude"}}
        self.assertEqual(calculate(plan, [None, "--", "2"]), "1")
        self.assertEqual(calculate(plan, []), "0")
        plan["predicate"]["operator"] = "invalid"
        with self.assertRaises(PipelineError):
            calculate(plan, [])

    def test_domain_includes_merged_cells_crossing_start_and_validates_labels(self):
        raw = {"operation": "calculate", "plan": {"op": "count", "refs": ["a", "b"],
               "domain": {"table_id": "t", "column": 0, "start_row": 2, "end_row": 4}}}
        self.assertEqual(solve_structured(self.q, self.tables, ResponseModel(raw))["answer"], "2")
        raw["plan"]["labels"] = ["b", "a"]
        with self.assertRaisesRegex(PipelineError, "对应数据行"):
            solve_structured(self.q, self.tables, ResponseModel(raw))

    def test_explicit_quantity_is_extracted_without_calculation(self):
        result = solve_structured(self.q, self.tables, ResponseModel({"operation": "extract", "values": [{"ref": "n", "text": "12.00"}]}))
        self.assertEqual(result["answer"], "12.00")
        self.assertFalse(result["computation_checked"])

    def test_extract_question_can_count_merged_cells_once(self):
        result = solve_structured(self.q, self.tables, ResponseModel({"operation": "calculate", "plan": {"op": "count", "refs": ["a", "b"]}}))
        self.assertEqual(result["answer"], "2")
        self.assertEqual(result["operation"], "calculate")
        self.assertTrue(result["computation_checked"])
        self.assertEqual([c["id"] for c in result["source_cells"]], ["a", "b"])
        self.assertEqual(result["source_cells"][0]["rowspan"], 2)
        self.assertEqual(self.q.question_type, "extract")

    def test_invalid_operations_and_plans_rejected(self):
        cases = [
            {"operation": "guess"},
            {"operation": "calculate", "values": [], "plan": {"op": "count", "refs": ["a"]}},
            {"operation": "extract", "values": [{"ref": "n", "text": "999"}]},
            {"operation": "calculate", "plan": {"op": "count", "refs": ["a", "a"]}},
            {"operation": "calculate", "plan": {"op": "count", "refs": ["missing"]}},
            {"operation": "calculate", "plan": {"op": "exec", "refs": ["a"]}},
            {"operation": "calculate", "plans": [None]},
            {"operation": "calculate", "plan": {}, "plans": []},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(PipelineError):
                solve_structured(self.q, self.tables, ResponseModel(raw))

    def test_explicit_vertical_multitable_structure_preserves_offsets(self):
        q = replace(self.q, question_type="structure", route="structure", answer_format="json")
        other = {"table_id": "u", "row_count": 2, "col_count": 3, "cells": [
            {"id": "u_h", "text": "第二表头", "row": 0, "col": 0, "rowspan": 1, "colspan": 3,
             "sources": [{"page": 1, "bbox": [0, 0, 100, 100]}]}]}
        raw = {"operation": "structure", "layout": "vertical", "parts": [
            {"table_id": "t", "cell_ids": ["h"]}, {"table_id": "u", "cell_ids": ["u_h"]}]}
        result = solve_structured(q, self.tables + [other], ResponseModel(raw))
        self.assertEqual((result["answer"]["row_count"], result["answer"]["col_count"]), (6, 3))
        self.assertEqual([c["row"] for c in result["answer"]["cells"]], [0, 4])
        self.assertEqual(result["structure_layout"], "vertical")
        self.assertEqual(other["cells"][0]["row"], 0)
        raw["parts"][1]["table_id"] = "t"
        with self.assertRaises(PipelineError):
            solve_structured(q, self.tables + [other], ResponseModel(raw))

    def test_structure_cannot_switch_to_calculate(self):
        with self.assertRaises(PipelineError):
            solve_structured(replace(self.q, question_type="structure", route="structure"), self.tables,
                             ResponseModel({"operation": "calculate", "plan": {"op": "count", "refs": ["a"]}}))

    def test_legacy_extract_response_remains_supported(self):
        result = solve_structured(self.q, self.tables, ResponseModel({"values": [{"ref": "n", "text": "12.00"}]}))
        self.assertEqual(result["operation"], "extract")

    def test_derived_answer_forces_review_even_when_disabled(self):
        for strategy in ("structured", "compare"):
            cfg = load_config()
            cfg["run"].update(single_pass=False, strategy=strategy, review=False, review_policy="on_risk", max_repairs=0)
            cfg["model"]["bbox_format"] = "pixel"
            model = ResponseModel({"operation": "calculate", "plan": {"op": "count", "refs": ["a", "b"]}})
            with tempfile.TemporaryDirectory() as folder:
                result = answer_question(self.q, self.pages, self.tables, model, cfg, Trace(Path(folder) / "trace.jsonl"), folder)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["validation_level"], "reviewed")
            self.assertEqual(sum(stage == "review" for stage, _ in model.calls), 1)
            review_payload = next(payload for stage, payload in model.calls if stage == "review")
            self.assertEqual(len(review_payload["candidates"]["structured"]["source_cells"]), 2)

    def test_plain_extract_still_skips_on_risk_review(self):
        cfg = load_config()
        cfg["run"].update(single_pass=False, strategy="structured", review=True, review_policy="on_risk", max_repairs=0)
        model = ResponseModel({"operation": "extract", "values": [{"ref": "n", "text": "12.00"}]})
        with tempfile.TemporaryDirectory() as folder:
            result = answer_question(self.q, self.pages, self.tables, model, cfg, Trace(Path(folder) / "trace.jsonl"), folder)
        self.assertEqual(result["validation_level"], "format_evidence")
        self.assertEqual([stage for stage, _ in model.calls], ["structured"])

    def test_derived_lookup_repair_requires_verification(self):
        raw = {"answer": "2", "evidence": [{"page": 1, "bbox": [0, 0, 100, 100]}]}
        with self.assertRaises(PipelineError):
            solve_direct(self.q, self.pages, ResponseModel(raw), repair={"requires_calculation": True})
        raw["verification"] = {"op": "count", "values": ["甲类", "乙类"]}
        result = solve_direct(self.q, self.pages, ResponseModel(raw), repair={"requires_calculation": True})
        self.assertTrue(result["computation_checked"])
