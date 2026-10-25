"""风险分级必须保守兼容旧协议，不能用提示等级掩盖未知风险。"""
import unittest

from table_qa.validation.review import classify_risks
from table_qa.schemas import PipelineError, Question
from table_qa.application.pipeline import review
from table_qa.config import load_config


class ReviewRiskTest(unittest.TestCase):
    def advisory(self):
        return {"severity": "advisory", "code": "evidence_granularity",
                "message": "整表框较粗", "rationale": "原图文字清晰且已逐格核对"}

    def test_graded_and_strict(self):
        self.assertEqual(classify_risks([self.advisory()])[0], [])
        self.assertEqual(len(classify_risks([self.advisory()], "strict")[0]), 1)

    def test_unknown_missing_rationale_and_legacy_block(self):
        for risk in ["筛选条件未验证", {**self.advisory(), "code": "filter_unverified"},
                     {**self.advisory(), "rationale": ""}, {**self.advisory(), "severity": "blocking"}]:
            self.assertEqual(len(classify_risks([risk])[0]), 1)
        for risks in [None, [None], [{}], [""], [{**self.advisory(), "severity": "low"}]]:
            with self.assertRaises(PipelineError):
                classify_risks(risks)

    def test_advisory_never_overrides_verdict_or_confidence(self):
        class Model:
            def ask(self, *args, **kwargs):
                return self.result
        model = Model()
        cfg = load_config()
        q = Question("x", "synthetic.png", "extract", "提取数值", answer_format="number")
        for verdict, confidence, expected in [("pass", .9, True), ("retry", .9, False), ("unknown", .9, False), ("pass", .2, False)]:
            model.result = {"verdict": verdict, "confidence": confidence, "preferred": "direct", "risks": [self.advisory()]}
            accepted, result = review(q, {"direct": {"answer": "6"}}, [], model, cfg)
            self.assertEqual(accepted, expected)
            self.assertEqual(result["accepted"], expected)
