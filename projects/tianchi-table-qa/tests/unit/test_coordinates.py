"""合成尺寸验证坐标契约，不以模拟测试代替真实模型定位验收。"""
import copy
import tempfile
import unittest
from pathlib import Path

from table_qa.config import load_config, validate_config
from table_qa.schemas import PipelineError, Question
from table_qa.validation.coordinates import to_pixels, reference_size
from table_qa.validation.answer import comparison_details
from table_qa.solving.direct import solve_direct, validate_evidence
from table_qa.application.routing import classify
from table_qa.application.runner import trace_stats
from table_qa.application.pipeline import review
from table_qa.infrastructure.tracing import Trace


class CoordinateTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_normalized_boxes_on_three_sizes(self):
        for w, h in [(515, 292), (1400, 900), (1488, 2105)]:
            for mode, scale in [("norm999", 999), ("norm1000", 1000)]:
                self.cfg["model"]["bbox_format"] = mode
                box = [scale * .1, scale * .2, scale * .8, scale * .9]
                self.assertEqual(to_pixels(box, w, h, self.cfg), [round(w * .1), round(h * .2), round(w * .8), round(h * .9)])
                self.assertEqual(to_pixels([0, 0, scale, scale], w, h, self.cfg), [0, 0, w, h])

    def test_resized_pixel_reference_maps_back(self):
        self.cfg["model"]["bbox_format"] = "resized_pixel"
        for w, h in [(515, 292), (20, 20), (4000, 3000)]:
            rw, rh = reference_size(w, h, self.cfg["model"])
            self.assertEqual(rw % 28, 0)
            self.assertEqual(rh % 28, 0)
            self.assertEqual(to_pixels([0, 0, rw, rh], w, h, self.cfg), [0, 0, w, h])
            self.assertEqual(to_pixels([0, 0, rw / 2, rh / 2], w, h, self.cfg), [0, 0, round(w / 2), round(h / 2)])

    def test_explicit_evidence_formats_keep_input_and_config_unchanged(self):
        pages = [{'page': 1, 'width': 1200, 'height': 400}]
        for mode, box in [('pixel', [120, 80, 1080, 360]), ('norm1000', [100, 200, 900, 900]),
                          ('norm999', [99.9, 199.8, 899.1, 899.1])]:
            item = {'page': 1, 'bbox_format': mode, 'image_size': [1200, 400], 'bbox': box}
            before = copy.deepcopy(item)
            self.assertEqual(validate_evidence([item], pages, self.cfg),
                             [{'page': 1, 'bbox': [120, 80, 1080, 360]}])
            self.assertEqual(item, before)
            self.assertEqual(self.cfg['model']['bbox_format'], 'norm999')

    def test_declared_evidence_rejects_wrong_frame_or_unknown_resize(self):
        pages = [{'page': 1, 'width': 1200, 'height': 400}]
        for extra in [{'bbox_format': 'pixel'}, {'bbox_format': 'pixel', 'image_size': [400, 1200]},
                      {'bbox_format': 'pixel', 'image_size': [1200.0, 400]},
                      {'bbox_format': 'resized_pixel', 'image_size': [1200, 400]},
                      {'image_size': [1200, 400]}, {'bbox_format': [], 'image_size': [1200, 400]},
                      {'bbox_format': None, 'image_size': [1200, 400]}]:
            with self.assertRaises(PipelineError):
                validate_evidence([{'page': 1, 'bbox': [0, 0, 100, 100], **extra}], pages, self.cfg)

    def test_legacy_evidence_does_not_guess_pixel_from_magnitude(self):
        pages = [{'page': 1, 'width': 1200, 'height': 400}]
        with self.assertRaises(PipelineError):
            validate_evidence([{'page': 1, 'bbox': [0, 0, 1200, 400]}], pages, self.cfg)

    def test_declared_pixel_does_not_relax_bounds(self):
        with self.assertRaises(PipelineError):
            validate_evidence([{'page': 1, 'bbox_format': 'pixel', 'image_size': [1200, 400],
                                'bbox': [0, 0, 1300, 400]}],
                              [{'page': 1, 'width': 1200, 'height': 400}], self.cfg)

    def test_declared_local_evidence_and_anchors_map_once(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['run'].update(single_pass=True, program_assembly=False)
        item = {'page': 1, 'bbox_format': 'pixel', 'image_size': [1200, 400], 'bbox': [120, 80, 1080, 360]}
        class Model:
            def ask(self, *args, **kwargs):
                return {'answer': '12.50', 'evidence': [item], 'anchors': {'row': item, 'column': item}}
        q = Question('s', 's.pdf', 'extract', '提取值', answer_format='number')
        result = solve_direct(q, [{'page': 1, 'width': 1200, 'height': 400, 'image': 'unused',
                                 'view_to_page': [[0, 1, 10], [-1, 0, 1300], [0, 0, 1]]}], Model(), cfg=cfg)
        expected = [90, 220, 370, 1180]
        self.assertEqual(result['evidence'][0]['bbox'], expected)
        self.assertEqual(result['anchors']['row']['bbox'], expected)
        self.assertEqual(result['anchors']['column']['bbox'], expected)

    def test_in_bounds_declared_box_is_not_proof_of_numeric_content(self):
        from table_qa.validation.grounding import local_checks
        cfg = copy.deepcopy(self.cfg)
        cfg['run'].update(single_pass=True, program_assembly=False)
        cfg['processing']['verify_pdf_evidence'] = True
        item = {'page': 1, 'bbox_format': 'pixel', 'image_size': [100, 100], 'bbox': [0, 0, 10, 10]}
        class Model:
            def ask(self, *args, **kwargs):
                return {'answer': '7', 'evidence': [item],
                        'grounding': {'table': 'T', 'rows': 'A', 'columns': 'Amount', 'scope': '单格'}}
        pages = [{'page': 1, 'width': 100, 'height': 100, 'image': 'unused',
                  'pdf_words': [{'text': '7', 'bbox': [80, 80, 90, 90]}]}]
        q = Question('s', 's.pdf', 'extract', '提取值', answer_format='number', route='lookup')
        candidate = solve_direct(q, pages, Model(), cfg=cfg)
        issues, _ = local_checks(q, candidate, pages, cfg)
        self.assertTrue(any('未支持答案数字' in message for message in issues))

    def test_clip_only_minor_overflow(self):
        self.cfg["model"]["bbox_format"] = "pixel"
        warnings = []
        self.assertEqual(to_pixels([-1, -1, 101, 101], 100, 100, self.cfg, warnings), [0, 0, 100, 100])
        self.assertTrue(warnings)
        for box in [[-30, 0, 100, 100], [100, 0, 0, 100], [0, 0, 0, 1], [0, 0, float("nan"), 1], [0, True, 1, 2]]:
            with self.assertRaises(PipelineError):
                to_pixels(box, 100, 100, self.cfg)

    def test_invalid_evidence_preserves_answer(self):
        class Model:
            def ask(self, *args, **kwargs):
                return {"answer": "12.50", "evidence": [{"page": 9, "bbox": [0, 0, 10, 10]}]}
        q = Question("x", "x", "extract", "提取值", answer_format="number")
        result = solve_direct(q, [{"page": 1, "width": 50, "height": 20, "image": "unused"}], Model(), cfg=self.cfg)
        self.assertEqual(result["answer"], "12.50")
        self.assertEqual(result["evidence_status"], "unlocalized")
        self.assertTrue(result["warnings"])

    def test_rule_regressions(self):
        for text, category in [("主表或主区域的标题", "metadata"),
                               ("提取'分类数量'对应的值", "lookup"), ("是否含数量列", "metadata"),
                               ("最高的catchment", "filter"), ("哪些天符合条件", "filter"),
                               ("计算A占B的比例是多少", "calculation"),
                               ("从表中提取数据并求和", "calculation")]:
            self.assertEqual(classify(Question("x", "x", "extract", text), self.cfg)["category"], category)

    def test_structure_diff_keeps_text_conflicts(self):
        q = Question("x", "x", "structure", "表头")
        a = {"row_count": 1, "col_count": 1, "cells": [{"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "A"}]}
        b = copy.deepcopy(a); b["cells"][0]["text"] = "B"
        self.assertEqual(comparison_details(q, a, b), {"dimensions_equal": True, "positions_equal": True, "text_equal": False})

    def test_invalid_config_rejected(self):
        for section, key, value in [("model", "bbox_format", "guess"), ("model", "bbox_clip_tolerance", .9),
                                    ("run", "min_review_confidence", float("nan"))]:
            cfg = copy.deepcopy(self.cfg); cfg[section][key] = value
            with self.assertRaises(ValueError):
                validate_config(cfg)

    def test_review_threshold_and_invalid_preference(self):
        class Model:
            preference = "direct"
            def ask(self, *args, **kwargs):
                return {"verdict": "pass", "confidence": .75, "risks": [], "preferred": self.preference}
        model = Model()
        q = Question("x", "x", "extract", "提取值")
        self.cfg["run"]["min_review_confidence"] = .8
        self.assertFalse(review(q, {"direct": {}}, [], model, self.cfg)[0])
        self.cfg["run"]["min_review_confidence"] = .7
        self.assertTrue(review(q, {"direct": {}}, [], model, self.cfg)[0])
        model.preference = "不存在"
        with self.assertRaises(PipelineError):
            review(q, {"direct": {}}, [], model, self.cfg)

    def test_route_usage_separates_shared_document(self):
        with tempfile.TemporaryDirectory() as folder:
            trace = Trace(Path(folder) / "trace.jsonl")
            trace.emit("model_attempt", stage="locate")
            with trace.scope(question_id="q", route_category="lookup"):
                trace.emit("model_attempt", stage="direct")
                trace.emit("coordinate_warning", stage="direct")
                trace.emit("model_response", usage={"total_tokens": 12})
            report = trace_stats(folder)
            self.assertEqual(report["route_usage"]["lookup"]["attempts_per_question"], 1)
            self.assertEqual(report["route_usage"]["lookup"]["total_tokens"], 12)
            self.assertIsNone(report["route_usage"]["shared_document"]["attempts_per_question"])
            self.assertEqual(report["coordinate_warning_count"], 1)
