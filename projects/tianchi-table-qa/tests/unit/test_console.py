"""控制台展示测试，不调用模型、不改历史报告。"""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from table_qa.cli import main, print_run_summary
from table_qa.config import load_config


class ConsoleTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.report = {'run_id': 'sample-run', 'selected_count': 2,
                       'local_states': {'success': 1, 'uncertain': 1},
                       'states': {'success': 888, 'uncertain': 20},
                       'inherited_not_rechecked_count': 906, 'duration_seconds': 96.52,
                       'model_attempts': 2, 'cache_hits': 0, 'usage': {'total_tokens': 16163},
                       'placeholder_count': 5, 'blank_count': 0, 'format_failed_ids': [],
                       'baseline_fallback_ids': ['599'], 'route_usage': {'details': 'retained'}}

    def run_cli(self, json_mode=False):
        def execute(*args, **kwargs):
            kwargs['progress'](0, 2, {'run_id': 'sample-run'})
            kwargs['progress'](1, 2, {'id': '599', 'status': 'uncertain'})
            return self.report
        out, err = io.StringIO(), io.StringIO()
        with patch('table_qa.cli.load_config', return_value=self.cfg), \
                patch('table_qa.cli.execute', side_effect=execute), redirect_stdout(out), redirect_stderr(err):
            status = main(['rerun', '--base', 'base-run', '--ids', '599,604'] + (['--json'] if json_mode else []))
        self.assertEqual(status, 0)
        return out.getvalue(), err.getvalue()

    def test_default_is_brief_and_distinguishes_inherited_results(self):
        out, err = self.run_cli()
        self.assertIn('[1/2] 题目 599：不确定', out)
        self.assertIn('本轮：2 题（成功 1，不确定 1）；继承 906 题', out)
        self.assertIn('Token 16,163', out)
        self.assertIn('占位答案 5 题', out)
        self.assertIn('回退旧答案 1 题', out)
        self.assertIn('report.json', out)
        self.assertNotIn('route_usage', out)
        self.assertNotIn('成功 888', out)
        self.assertEqual(err, '')

    def test_json_stdout_is_parseable_and_progress_goes_to_stderr(self):
        out, err = self.run_cli(True)
        self.assertEqual(json.loads(out), self.report)
        self.assertIn('[1/2] 题目 599', err)
        self.assertNotIn('本轮：', out)

    def test_summary_does_not_change_report_and_zero_is_valid(self):
        report = copy.deepcopy(self.report)
        report.update(selected_count=0, local_states={}, duration_seconds=0, placeholder_count=0,
                      baseline_fallback_ids=[])
        before = copy.deepcopy(report)
        with redirect_stdout(io.StringIO()) as out:
            print_run_summary(report, self.cfg)
        self.assertEqual(report, before)
        self.assertIn('无新结果', out.getvalue())
        self.assertNotIn('注意', out.getvalue())
