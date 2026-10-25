"""模拟 SDK 验证网络入口；测试中不发生任何 HTTP 请求。"""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from openai import AuthenticationError, RateLimitError

from table_qa.config import load_config, validate_config
from table_qa.infrastructure.model_client import ModelClient
from table_qa.infrastructure.tracing import Trace
from table_qa.schemas import BudgetExceeded, PipelineError, RunAborted


def response(content='{"answer":"ok"}', finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
                           usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 5}))


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = load_config()
        # 客户端旧阶段语义/缓存兼容；新配置的禁复核与thinking另设用例。
        self.cfg['run'].update(single_pass=False)
        self.cfg['model']['stages'] = {'recognize': {'timeout': 240}}
        self.cfg["paths"]["cache"] = str(self.root / "cache")
        self.cfg["model"].update(vision="qwen-test", text="qwen-test", requests_per_minute=600000)
        self.env = patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-only-fake-key"})
        self.env.start()
        self.sdk_patch = patch("openai.OpenAI")
        self.sdk = self.sdk_patch.start().return_value
        self.sdk.chat.completions.create.return_value = response()
        self.sleep = patch("table_qa.infrastructure.model_client.time.sleep")
        self.sleep.start()
        self.client = ModelClient(self.cfg, Trace(self.root / "trace.jsonl"), self.root)

    def tearDown(self):
        self.sleep.stop(); self.sdk_patch.stop(); self.env.stop(); self.tmp.cleanup()

    def test_cache_reuses_and_fresh_bypasses(self):
        self.client.ask("direct", {"question": "one"})
        self.client.ask("direct", {"question": "one"})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 1)
        self.client.ask("direct", {"question": "one"}, fresh=True)
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)

    def test_single_pass_sends_images_first_and_no_thinking_history(self):
        from PIL import Image
        image = self.root / 'one.png'
        Image.new('RGB', (20, 20), 'white').save(image)
        self.cfg['run']['single_pass'] = True
        self.cfg['model']['stages']['direct'] = {'enable_thinking': False}
        self.client.ask('direct', {'question': '读取数据', 'reasoning_content': 'DO_NOT_SEND',
                                   'review_history': [{'answer': 'OLD_ANSWER'}]}, [image])
        kwargs = self.sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs['extra_body'], {'enable_thinking': False})
        self.assertEqual([m['role'] for m in kwargs['messages']], ['system', 'user'])
        content = kwargs['messages'][1]['content']
        self.assertEqual(content[0]['type'], 'image_url')
        self.assertEqual(content[-1]['type'], 'text')
        self.assertNotIn('DO_NOT_SEND', content[-1]['text'])
        self.assertNotIn('OLD_ANSWER', content[-1]['text'])

    def test_structure_selects_dedicated_short_prompt_for_both_stages(self):
        self.cfg['run']['single_pass'] = True
        for stage in ('direct', 'repair'):
            self.client.ask(stage, {'question': {'answer_contract': 'table'}})
            text = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
            self.assertIn(self.cfg['prompts'][stage + '_structure'].strip(), text)
            self.assertNotIn('scope_check', text)

    def test_prompt_change_invalidates_cache(self):
        self.client.ask("direct", {})
        self.cfg["prompts"]["direct"] += " version2"
        self.client.ask("direct", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)

    def test_json_mode_and_facts_prompt_are_sent_and_in_cache_key(self):
        self.cfg['run']['single_pass'] = True
        self.cfg['model']['stages']['direct'] = {'enable_thinking': False, 'json_object': True}
        self.client.ask('direct', {'single_pass': True, 'response_mode': 'table_facts'})
        kwargs = self.sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs['response_format'], {'type': 'json_object'})
        self.assertIn(self.cfg['prompts']['table_facts_response'].strip(), kwargs['messages'][1]['content'][0]['text'])
        self.cfg['model']['stages']['direct']['json_object'] = False
        self.client.ask('direct', {'single_pass': True, 'response_mode': 'table_facts'})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)

    def test_visual_attribute_prompt_does_not_use_literal_lookup_instructions(self):
        self.cfg['run']['single_pass'] = True
        self.client.ask('direct', {'single_pass': True, 'visual_task': {'criterion': '是否横向拍摄'}})
        text = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
        self.assertIn(self.cfg['prompts']['direct_visual'].strip(), text)
        self.assertNotIn('单值提取还需anchors=', text)

    def test_focused_caption_uses_current_view_reading_prompt(self):
        self.cfg['run']['single_pass'] = True
        self.client.ask('direct', {'single_pass':True,'input_views':[{'caption_focused':True}]})
        text=self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
        self.assertIn(self.cfg['prompts']['direct_caption'].strip(),text)

    def test_coordinate_contract_invalidates_cache(self):
        self.client.ask("direct", {})
        self.cfg["model"]["bbox_format"] = "norm1000"
        self.client.ask("direct", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)
        message = self.sdk.chat.completions.create.call_args.kwargs["messages"][1]["content"][0]["text"]
        self.assertIn("coordinate_contract", message)
        self.assertIn("norm1000", message)

    def test_evidence_declaration_is_scoped_to_single_pass_answer(self):
        self.cfg['run']['single_pass'] = True
        for stage in ('direct', 'repair', 'locate'):
            self.client.ask(stage, {'single_pass': True})
            message = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
            # 系统提示单独发送；这里只检查用户payload中的真实契约。
            self.assertEqual('"evidence_declaration"' in message, stage != 'locate')

    def test_refresh_all_ignores_existing_cache(self):
        self.client.ask("locate", {})
        self.cfg["run"]["refresh_model_cache"] = True
        self.client.ask("locate", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)

    def test_stage_refresh_preserves_other_cached_responses(self):
        for stage in ("locate", "recognize", "boundary", "direct"):
            self.client.ask(stage, {})
        self.cfg["run"]["refresh_stages"] = ["recognize"]
        for stage in ("locate", "recognize", "boundary", "direct"):
            self.client.ask(stage, {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 5)

    def test_changed_document_refresh_is_scoped_to_recognition(self):
        self.client.ask("locate", {})
        self.client.ask("direct", {})
        self.client.set_document_refresh(True)
        self.client.ask("locate", {})
        self.client.ask("direct", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 3)
        self.client.set_document_refresh(False)
        self.client.ask("locate", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 3)

    def test_invalid_stage_refresh_rejected(self):
        for stages in ("recognize", ["unknown"], [True]):
            self.cfg["run"]["refresh_stages"] = stages
            with self.subTest(stages=stages), self.assertRaises(ValueError):
                validate_config(self.cfg)

    def test_cli_scoped_cache_flags_and_question_type_selection(self):
        from table_qa.cli import main
        from table_qa.schemas import Question
        questions = [Question("a", "a.png", "structure", "恢复表头"),
                     Question("b", "a.png", "extract", "提取数值")]
        with patch("table_qa.cli.load_config", return_value=copy.deepcopy(self.cfg)), \
                patch("table_qa.cli.inspect_inputs", return_value=(questions, {"ok": True})), \
                patch("table_qa.cli.execute", return_value={}) as execute, patch("builtins.print"):
            code = main(["rerun", "--base", "old", "--question-type", "structure", "--reuse-recognition",
                         "--reuse-answer-cache", "--refresh-stages", "recognize,boundary", "--block-height", "900",
                         "--review-policy", "always", "--accept-document-changes"])
        self.assertEqual(code, 0)
        cfg, mode, ids, base = execute.call_args.args
        self.assertEqual((mode, ids, base), ("rerun", ["a"], "old"))
        self.assertEqual(cfg["run"]["refresh_stages"], ["boundary", "recognize"])
        self.assertEqual(cfg["run"]["recognition_block_height"], 900)
        self.assertEqual(cfg["run"]["review_policy"], "always")
        self.assertTrue(cfg["run"]["accept_document_changes"])
        self.assertEqual(cfg["processing"], self.cfg["processing"])
        self.assertTrue(cfg["run"]["reuse_recognition"])
        self.assertTrue(cfg["run"]["reuse_answer_cache"])

    def test_cli_rejects_ambiguous_selection_and_nonexecution_refresh(self):
        from table_qa.cli import main
        for args in (["rerun", "--question-type", "structure", "--ids", "1"],
                     ["export", "--refresh-stages", "recognize"], ["run", "--reuse-recognition"]):
            with self.subTest(args=args), patch("builtins.print"), patch("table_qa.cli.execute") as execute:
                self.assertEqual(main(args), 2)
                execute.assert_not_called()

    def test_model_change_invalidates_cache(self):
        self.client.ask("direct", {})
        self.cfg["model"]["vision"] = "qwen-other-test"
        self.client.ask("direct", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)

    def test_runtime_paths_do_not_invalidate_model_cache(self):
        self.client.ask("direct", {"source": {"image": "/run/a", "page": 1}})
        self.client.ask("direct", {"source": {"image": "/run/b", "page": 1}})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 1)

    def test_bad_json_not_cached(self):
        self.sdk.chat.completions.create.return_value = response("not json")
        with self.assertRaises(PipelineError):
            self.client.ask("direct", {})
        self.sdk.chat.completions.create.return_value = response()
        self.client.ask("direct", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 2)

    def test_field_list_prompt_has_legacy_fallback(self):
        self.cfg['run']['single_pass'] = True
        payload = {'single_pass': True, 'task_requirements': {'requested_fields': ['名称', '流量']}}
        self.client.ask('direct', payload)
        text = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
        self.assertIn(self.cfg['prompts']['direct_fields'], text)
        self.cfg['prompts'].pop('direct_fields')
        self.client.ask('direct', payload, fresh=True)
        text = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
        self.assertIn(self.cfg['prompts']['direct'], text)

    def test_mixed_fields_take_priority_over_visual_prompt(self):
        self.cfg['run']['single_pass'] = True
        payload = {'single_pass': True, 'visual_task': {'criterion': '价格与是否含字段'},
                   'task_requirements': {'requested_fields': ['最低价格', '是否含编号列']}}
        for stage in ('direct', 'repair'):
            self.client.ask(stage, payload)
            text = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
            self.assertIn(self.cfg['prompts']['direct_fields'].strip(), text)
            self.assertNotIn(self.cfg['prompts']['direct_visual'].strip(), text)
        payload['response_mode'] = 'table_facts'
        self.client.ask('direct', payload)
        text = self.sdk.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['text']
        self.assertIn(self.cfg['prompts']['table_facts_response'].strip(), text)

    def test_truncation_rejected(self):
        self.sdk.chat.completions.create.return_value = response(finish="length")
        with self.assertRaisesRegex(PipelineError, "截断"):
            self.client.ask("direct", {})

    def test_auth_stops_without_retry(self):
        raw = httpx.Response(401, request=httpx.Request("POST", "https://dashscope.aliyuncs.com"))
        self.sdk.chat.completions.create.side_effect = AuthenticationError("bad key", response=raw, body={})
        with self.assertRaises(RunAborted):
            self.client.ask("direct", {})
        self.assertEqual(self.sdk.chat.completions.create.call_count, 1)

    def test_rate_limit_retry_is_counted(self):
        raw = httpx.Response(429, request=httpx.Request("POST", "https://dashscope.aliyuncs.com"))
        self.sdk.chat.completions.create.side_effect = [RateLimitError("limited", response=raw, body={}), response()]
        self.client.ask("direct", {})
        self.assertEqual(self.client.used_calls, 2)

    def test_budget_blocks_request(self):
        self.cfg["run"]["max_model_calls"] = 1
        self.client.ask("direct", {})
        with self.assertRaises(BudgetExceeded):
            self.client.ask("direct", {}, fresh=True)
        self.assertEqual(self.sdk.chat.completions.create.call_count, 1)

    def test_rejects_non_aliyun_endpoint_and_non_qwen(self):
        self.cfg["model"]["base_url"] = "https://example.com/v1"
        with self.assertRaises(ValueError):
            validate_config(self.cfg)
        self.cfg["model"]["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.cfg["model"]["vision"] = "other-model"
        with self.assertRaises(ValueError):
            validate_config(self.cfg)
