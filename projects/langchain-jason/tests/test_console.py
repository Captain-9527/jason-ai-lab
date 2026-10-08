"""终端多轮对话和单轮追踪的回归测试。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from app import build_parser, main
from cli.console import run_console
from core.config_loader import Settings
from core.tracing import TraceRecorder
from modes.react_mode import ReactMode


class FakeAgent:
    """只模拟消息传递，不调用模型或外部工具。"""

    def __init__(self):
        self.calls: list[list[dict[str, str]]] = []

    def invoke(self, payload, config=None):
        self.calls.append(payload["messages"])
        return {"messages": [SimpleNamespace(content=f"回答{len(self.calls)}")]}


class ConsoleTest(unittest.TestCase):
    def test_parser_accepts_interactive_flag(self):
        self.assertTrue(build_parser().parse_args(["--interactive"]).interactive)

    def test_interactive_rejects_non_react_mode(self):
        with redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(2, main(["--interactive", "--mode", "lcel"]))
        self.assertIn("仅支持 ReAct", errors.getvalue())

    def test_two_turns_share_memory_and_have_separate_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = TraceRecorder(Path(tmp) / "trace.jsonl")
            mode = ReactMode(Settings({}), None, None, recorder=recorder)
            fake_agent = FakeAgent()
            mode._agent = fake_agent
            inputs = iter(["第二问", "退出"])
            with redirect_stdout(io.StringIO()) as output:
                status = run_console(
                    mode,
                    recorder,
                    session_id="sample",
                    directory=tmp,
                    initial_prompt="第一问",
                    input_reader=lambda _: next(inputs),
                )

            self.assertEqual(0, status)
            self.assertEqual(2, len(fake_agent.calls))
            self.assertEqual(1, len(fake_agent.calls[0]))
            self.assertIn(tmp, fake_agent.calls[0][0]["content"])
            self.assertEqual(3, len(fake_agent.calls[1]))
            self.assertEqual("回答1", fake_agent.calls[1][1]["content"])
            self.assertEqual("第二问", fake_agent.calls[1][2]["content"])
            self.assertEqual(4, len(mode.sessions.get("sample").messages))
            self.assertIn("助手> 回答2", output.getvalue())

            entries = [json.loads(line) for line in recorder.path.read_text().splitlines()]
            starts = [entry for entry in entries if entry["event"] == "task.started"]
            completions = [entry for entry in entries if entry["event"] == "task.completed"]
            self.assertEqual(2, len(starts))
            self.assertEqual(2, len(completions))
            self.assertNotEqual(starts[0]["trace_id"], starts[1]["trace_id"])
            self.assertTrue(all(entry["session_id"] == "sample" for entry in entries))


if __name__ == "__main__":
    unittest.main()
