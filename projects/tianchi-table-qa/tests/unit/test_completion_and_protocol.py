"""空答案、识别传输协议及阶段参数的离线回归，不触碰真实运行。"""
import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from table_qa.cli import main
from table_qa.config import load_config, validate_config
from table_qa.schemas import Question, PipelineError
from table_qa.validation.answer import format_answer, validate_candidate_answer, is_completed
from table_qa.validation.table import decode_recognized_table
from tests.unit import test_client as fixtures


class CompletionTest(unittest.TestCase):
    def setUp(self):
        self.q = Question("1", "sample.png", "extract", "表头", answer_format="string")

    def test_empty_rejected_without_changing_historical_formatting(self):
        for value in ("", " \n\t", "　"):
            self.assertEqual(format_answer(self.q, value), "")
            with self.assertRaises(PipelineError):
                validate_candidate_answer(self.q, value)
            record = {"status": "success", "answer": value}
            before = copy.deepcopy(record)
            self.assertFalse(is_completed(self.q, record))
            self.assertEqual(record, before)

    def test_completion_accepts_zero_array_and_blank_table_cell(self):
        cases = [(self.q, "0"), (replace(self.q, answer_format="number"), 0),
                 (replace(self.q, answer_format="json_array"), []),
                 (replace(self.q, question_type="structure"), decode_recognized_table(
                     {"row_count": 1, "col_count": 1, "cells": [["", 0, 0, 1, 1]]}))]
        for q, answer in cases:
            self.assertTrue(is_completed(q, {"status": "success", "answer": answer}))
        for record in ({}, {"status": "uncertain", "answer": "ok"},
                       {"status": "success", "answer": None}, {"status": "success", "answer": {}}):
            self.assertFalse(is_completed(self.q, record))

    def test_failed_selector_includes_empty_and_noop_does_not_execute(self):
        from dataclasses import asdict
        from pathlib import Path
        cfg = load_config()
        snapshot = {"dataset": {"questions": [asdict(replace(self.q, id=str(i))) for i in range(1, 5)]},
                    "config": cfg}
        previous = {"1": {"status": "success", "answer": ""},
                    "2": {"status": "success", "answer": "ok"},
                    "3": {"status": "uncertain", "answer": "ok"}}
        with patch("table_qa.cli.load_config", return_value=cfg), \
             patch("table_qa.cli.run_path", return_value=Path("/unused")), \
             patch("table_qa.cli.read_results", return_value=previous), \
             patch("table_qa.infrastructure.storage.read_json", return_value=snapshot), \
             patch("table_qa.cli.execute", return_value={"ok": True}) as execute, patch("builtins.print"):
            self.assertEqual(main(["rerun", "--base", "old", "--failed"]), 0)
            self.assertEqual(execute.call_args.args[2], ["1", "3", "4"])
            execute.reset_mock()
            previous.update({str(i): {"status": "success", "answer": "ok"} for i in range(1, 5)})
            self.assertEqual(main(["rerun", "--base", "old", "--failed"]), 0)
            execute.assert_not_called()

    def test_recognition_protocol_and_raw_immutability(self):
        raw = {"row_count": 2, "col_count": 1, "cells": [["", 0, 0, 1, 1], [None, 1, 0, 1, 1]]}
        before = copy.deepcopy(raw)
        decoded = decode_recognized_table(raw)
        self.assertEqual(raw, before)
        self.assertEqual(decoded["cells"][0]["text"], "")
        self.assertIsNone(decoded["cells"][1]["text"])
        self.assertEqual(decode_recognized_table(decoded), decoded)
        self.assertLess(len(json.dumps(raw)), len(json.dumps(decoded)))
        for cells in ([["x", 0, 0, 1]], [["x", False, 0, 1, 1]],
                      [["x", 2, 0, 1, 1]], [["x", 0, 0, 2, 1], ["y", 1, 0, 1, 1]]):
            with self.assertRaises(PipelineError):
                decode_recognized_table({**raw, "cells": cells})


class StageOptionsTest(unittest.TestCase):
    setUp = fixtures.ClientTest.setUp
    tearDown = fixtures.ClientTest.tearDown

    def test_timeout_is_stage_local_and_not_in_cache_key(self):
        self.client.ask("recognize", {})
        self.assertEqual(self.sdk.chat.completions.create.call_args.kwargs["timeout"], 240)
        self.cfg["model"]["stages"]["recognize"]["timeout"] = 180
        self.client.ask("recognize", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 1)
        self.client.ask("direct", {})
        kwargs = self.sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 120)
        self.assertNotIn("extra_body", kwargs)
        self.assertEqual(self.cfg["model"]["timeout"], 120)

    def test_thinking_and_token_limit_are_stage_local_cache_inputs(self):
        self.client.ask("review", {})
        self.cfg["model"]["stages"]["review"] = {"enable_thinking": False, "max_tokens": 4096}
        self.client.ask("review", {})
        kwargs = self.sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})
        self.assertEqual(kwargs["max_tokens"], 4096)
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)
        self.cfg["model"]["stages"]["review"] = {"enable_thinking": True, "thinking_budget": 1024}
        self.client.ask("review", {})
        self.cfg["model"]["stages"]["review"]["thinking_budget"] = 2048
        self.client.ask("review", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 4)
        self.assertEqual(self.sdk.chat.completions.create.call_args.kwargs["extra_body"]["thinking_budget"], 2048)
        self.client.ask("direct", {})
        self.assertNotIn("extra_body", self.sdk.chat.completions.create.call_args.kwargs)

    def test_invalid_stage_settings_rejected(self):
        for stages in ([], {"unknown": {}}, {"review": {"unknown": 1}},
                       {"review": {"timeout": float("nan")}}, {"review": {"timeout": True}},
                       {"review": {"max_tokens": 0}}, {"review": {"enable_thinking": "false"}},
                       {"review": {"thinking_budget": 1024}},
                       {"review": {"enable_thinking": False, "thinking_budget": 1024}}):
            self.cfg["model"]["stages"] = stages
            with self.assertRaises(ValueError):
                validate_config(self.cfg)
        self.cfg["model"]["stages"] = {}
        self.cfg["processing"]["recognize_cell_format"] = "invalid"
        with self.assertRaises(ValueError):
            validate_config(self.cfg)
