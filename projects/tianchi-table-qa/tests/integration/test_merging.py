"""合并只消费模拟运行，不请求服务；来源文件保持不变。"""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.integration import test_pipeline as fixtures
from table_qa.application.merging import merge_runs
from table_qa.application.runner import execute, export_run
from table_qa.infrastructure.storage import read_json, read_results, atomic_json, save_results


class MergingTest(unittest.TestCase):
    def setUp(self):
        fixtures.IntegrationTest.setUp(self)
        # 保留旧快照留空策略的兼容性测试；非空策略另行验证。
        self.cfg["run"]["fill_missing_answers"] = False
    tearDown = fixtures.IntegrationTest.tearDown

    def run_sample(self, ids):
        report = execute(self.cfg, ids=ids, model_factory=fixtures.FakeModel)
        return report["run_id"]

    def folder(self, rid):
        return Path(self.cfg["paths"]["outputs"]) / rid

    def test_explicit_nonempty_export_preserves_results(self):
        a = self.run_sample(["1"])
        path = self.folder(a) / "results.jsonl"
        original = path.read_bytes()
        with patch("table_qa.application.runner.ModelClient", side_effect=AssertionError("不能调用模型")):
            report = export_run(self.cfg, a, fill_missing=True)
        self.assertEqual(report["blank_count"], 0)
        self.assertEqual(report["placeholder_ids"], ["2", "3"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(export_run(self.cfg, a)["blank_count"], 2)

    def test_nonempty_merge_preserves_missing_results(self):
        self.cfg["run"]["fill_missing_answers"] = True
        a, b = self.run_sample(["1"]), self.run_sample(["2"])
        report = merge_runs(self.cfg, [a, b])
        self.assertEqual(report["blank_count"], 0)
        self.assertEqual(report["placeholder_ids"], ["3"])
        self.assertNotIn("3", read_results(self.folder(report["run_id"])))

    def test_disjoint_merge_and_export_without_model(self):
        a, b = self.run_sample(["1"]), self.run_sample(["2"])
        original = (self.folder(a) / "results.jsonl").read_bytes()
        with patch("table_qa.application.runner.ModelClient", side_effect=AssertionError("不能调用模型")):
            result = merge_runs(self.cfg, [a, b])
            self.assertEqual(result["submitted_count"], 2)
            self.assertEqual(result["model_attempts"], 0)
            self.assertTrue(result["ok"])
            self.assertEqual(export_run(self.cfg, result["run_id"])["submitted_count"], 2)
        rows = read_results(self.folder(result["run_id"]))
        self.assertEqual(rows["1"]["source_run_id"], a)
        self.assertEqual(rows["2"]["source_run_id"], b)
        self.assertEqual((self.folder(a) / "results.jsonl").read_bytes(), original)
        with self.assertRaisesRegex(ValueError, "不能 resume"):
            execute(self.cfg, "resume", base=result["run_id"], model_factory=fixtures.FakeModel)
        rerun = execute(self.cfg, "rerun", ids=["3"], base=result["run_id"], model_factory=fixtures.FakeModel)
        self.assertEqual(rerun["submitted_count"], 3)

    def test_duplicate_and_conflict_requires_explicit_preference(self):
        a, b = self.run_sample(["1"]), self.run_sample(["1"])
        same = merge_runs(self.cfg, [a, b])
        self.assertEqual(same["submitted_count"], 1)
        self.assertFalse(same["conflict_ids"])
        rows = read_results(self.folder(b))
        rows["1"]["answer"] = "11"
        save_results(self.folder(b), rows)
        conflict = merge_runs(self.cfg, [a, b])
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["conflict_ids"], ["1"])
        self.assertEqual(conflict["submitted_count"], 0)
        preferred = merge_runs(self.cfg, [a, b], b)
        self.assertTrue(preferred["ok"])
        row = read_results(self.folder(preferred["run_id"]))["1"]
        self.assertEqual(row["answer"], "11")
        self.assertEqual(row["source_run_id"], b)

    def test_rejects_incompatible_snapshots(self):
        a, b = self.run_sample(["1"]), self.run_sample(["2"])
        path = self.folder(b) / "snapshot.json"
        original = read_json(path)
        for field in ("dataset_hash", "template_hash", "policy"):
            snap = copy.deepcopy(original)
            if field == "policy":
                snap["config"]["run"]["submit_uncertain"] = not snap["config"]["run"]["submit_uncertain"]
            else:
                snap[field] = "changed"
            atomic_json(path, snap)
            with self.assertRaises(ValueError):
                merge_runs(self.cfg, [a, b])
        atomic_json(path, original)
        with self.assertRaises(ValueError):
            merge_runs(self.cfg, [a, a])
        with self.assertRaises(ValueError):
            merge_runs(self.cfg, [a, b], "unknown")
        with self.assertRaises(ValueError):
            merge_runs(self.cfg, [a, "../escape"])

    def test_invalid_answer_reported_and_decimal_presentation_preserved(self):
        a, b = self.run_sample(["1"]), self.run_sample(["1"])
        rows = read_results(self.folder(b))
        rows["1"]["answer"] = "10.00"
        save_results(self.folder(b), rows)
        self.assertEqual(merge_runs(self.cfg, [a, b])["conflict_ids"], ["1"])
        rows["1"]["answer"] = "不是数字"
        save_results(self.folder(b), rows)
        result = merge_runs(self.cfg, [a, b])
        self.assertFalse(result["ok"])
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["invalid_answers"][0]["run_id"], b)
