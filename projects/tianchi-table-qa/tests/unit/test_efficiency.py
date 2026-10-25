"""输入瘦身、完整性保护和旧日志统计的离线回归。"""
import copy
import json
import unittest
import tempfile
from pathlib import Path
from dataclasses import replace

from table_qa.infrastructure.metrics import efficiency_stats
from table_qa.processing.long_table import merge_tables
from table_qa.schemas import PipelineError
from table_qa.solving.structured import compact_tables, solve_structured
from table_qa.application.pipeline import answer_question
from table_qa.config import load_config
from table_qa.infrastructure.tracing import Trace
from tests.unit import test_structured_operations as fixtures


class EfficiencyTest(unittest.TestCase):
    setUp = fixtures.StructuredOperationsTest.setUp

    def test_compact_keeps_semantics_and_local_evidence(self):
        self.tables[0].update(notes=["单位：万元"], title="合成表", unit="万元")
        before = copy.deepcopy(self.tables)
        compact = compact_tables(self.tables)
        self.assertEqual(self.tables, before)
        self.assertNotIn("segments", compact[0])
        self.assertNotIn("sources", compact[0]["cells"][0])
        self.assertEqual(compact[0]["cells"][1]["rowspan"], 2)
        self.assertEqual(compact[0]["notes"], ["单位：万元"])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(self.tables)))
        model = fixtures.ResponseModel({"operation": "extract", "values": [{"ref": "n", "text": "12.00"}]})
        result = solve_structured(self.q, self.tables, model)
        self.assertTrue(result["evidence"])
        self.assertNotIn("sources", model.calls[0][1]["tables"][0]["cells"][0])

    def test_incomplete_allows_extract_not_computation_or_structure(self):
        self.tables[0].update(incomplete=True, integrity_warnings=["边界未恢复"])
        model = fixtures.ResponseModel({"operation": "extract", "values": [{"ref": "n", "text": "12.00"}]})
        result = solve_structured(self.q, self.tables, model)
        self.assertTrue(result["requires_review"])
        self.assertTrue(result["warnings"])
        self.assertEqual(model.calls[0][1]["question"]["allowed_operations"], ["extract"])
        model = fixtures.ResponseModel({"operation": "calculate", "plan": {"op": "count", "refs": ["a", "b"]}})
        with self.assertRaises(PipelineError):
            solve_structured(self.q, self.tables, model)
        with self.assertRaisesRegex(PipelineError, "未恢复"):
            solve_structured(replace(self.q, question_type="structure"), self.tables, model)

    def test_header_normalization_does_not_rewrite_text_or_overlap_values(self):
        left = {"row_count": 2, "col_count": 1, "cells": [
            {"text": "其他变动（注1）", "row": 0, "col": 0, "rowspan": 1, "colspan": 1},
            {"text": "10", "row": 1, "col": 0, "rowspan": 1, "colspan": 1}]}
        right = copy.deepcopy(left)
        right["cells"][0]["text"] = "其他变动(注 1)"
        right["cells"][1]["text"] = "20"
        relation = {"same_table": True, "column_order": [0], "drop_header_rows": 1}
        merged = merge_tables(left, right, relation)
        self.assertEqual(merged["cells"][0]["text"], "其他变动（注1）")
        self.assertEqual(right["cells"][0]["text"], "其他变动(注 1)")
        right["cells"][0]["text"] = "另一指标"
        with self.assertRaises(PipelineError):
            merge_tables(left, right, relation)
        right["cells"][0]["text"] = "10.0"
        with self.assertRaises(PipelineError):
            merge_tables(left, right, {"same_table": True, "column_order": [0], "overlap_rows": 1})

    def test_partial_extraction_forces_review_even_if_disabled(self):
        self.tables[0]["incomplete"] = True
        model = fixtures.ResponseModel({"operation": "extract", "values": [{"ref": "n", "text": "12.00"}]})
        cfg = load_config()
        cfg["run"].update(single_pass=False, strategy="structured", review=False)
        with tempfile.TemporaryDirectory() as folder:
            result = answer_question(self.q, self.pages, self.tables, model, cfg, Trace(Path(folder) / "trace"), folder)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["validation_level"], "reviewed")
        self.assertEqual(sum(stage == "review" for stage, _ in model.calls), 1)

    def test_metrics_correlates_legacy_responses_and_excludes_inherited(self):
        events = [
            {"event": "model_attempt", "request_id": "a", "stage": "review"},
            {"event": "model_response", "request_id": "a", "duration_seconds": 3,
             "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                       "completion_tokens_details": {"reasoning_tokens": 20},
                       "prompt_tokens_details": {"cached_tokens": 40}}},
            {"event": "model_attempt", "request_id": "b", "stage": "recognize"},
            {"event": "model_error", "request_id": "b", "duration_seconds": 120, "error_type": "APITimeoutError"},
            {"event": "cache_hit", "stage": "recognize"}, {"event": "table_merge_fallback"}]
        results = {"1": {"routing": {"strategy": "compare"}, "candidates": {"structured": {}}, "review": {"verdict": "pass"}},
                   "2": {"inherited_from": "old", "routing": {"strategy": "compare"}, "candidates": {}}}
        stats = efficiency_stats(events, results)
        review = stats["stage_usage"]["review"]
        self.assertEqual(review["reasoning_tokens"], 20)
        self.assertEqual(review["answer_tokens_estimate"], 10)
        self.assertEqual(review["server_cached_tokens"], 40)
        rec = stats["stage_usage"]["recognize"]
        self.assertEqual(rec["timeouts"], 1)
        self.assertEqual(rec["error_seconds"], 120)
        self.assertEqual(rec["local_cache_hits"], 1)
        self.assertIsNone(rec["reasoning_tokens"])
        self.assertEqual(stats["candidate_quality"]["structured_available_rate"], 1)
        self.assertEqual(stats["candidate_quality"]["local_question_count"], 1)
        self.assertEqual(stats["table_merge_fallback_count"], 1)
