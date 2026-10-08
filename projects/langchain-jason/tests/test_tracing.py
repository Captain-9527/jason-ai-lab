from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from agent.trace_callback import AgentTraceCallback
from core.config_loader import Settings
from core.tracing import TraceRecorder, build_trace_recorder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult


class TracingTest(unittest.TestCase):
    def test_records_one_json_object_per_line_and_redacts_sensitive_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            recorder = TraceRecorder(path)
            trace_id = recorder.start_trace(session_id="session-1", mode="lcel")
            recorder.record(
                event="tool.completed",
                component="example",
                status="success",
                input_data={
                    "directory": "/Users/example/Documents/private",
                    "api_key": "must-not-appear",
                },
                output_data={"count": 3},
                duration_ms=12.34567,
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(trace_id, entry["trace_id"])
            self.assertEqual("<redacted>", entry["input"]["api_key"])
            self.assertNotIn("/Users/example", entry["input"]["directory"])
            self.assertEqual(12.346, entry["duration_ms"])

    def test_build_recorder_rejects_path_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            settings = Settings(
                {
                    "tracing": {
                        "enabled": True,
                        "file": "../outside.jsonl",
                    }
                }
            )
            with self.assertRaises(Exception):
                build_trace_recorder(settings, project)

    def test_payload_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            recorder = TraceRecorder(path, payload_mode="none")
            recorder.start_trace(session_id="session-1", mode="hybrid")
            recorder.record(
                event="task.completed",
                component="app",
                status="success",
                input_data={"private": "value"},
                output_data={"result": "value"},
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertIsNone(entry["input"])
            self.assertIsNone(entry["output"])

    def test_agent_callback_records_model_input_output_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            recorder = TraceRecorder(path)
            recorder.start_trace(session_id="session-1", mode="react")
            callback = AgentTraceCallback(recorder)
            run_id = uuid4()
            callback.on_chat_model_start(
                {"name": "fake-chat-model"},
                [[HumanMessage(content="hello")]],
                run_id=run_id,
            )
            callback.on_llm_end(
                LLMResult(
                    generations=[[ChatGeneration(message=AIMessage(content="world"))]],
                    llm_output={"token_usage": {"total_tokens": 2}},
                ),
                run_id=run_id,
            )
            entries = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["model.started", "model.completed"], [e["event"] for e in entries])
            self.assertEqual("hello", entries[0]["input"]["messages"][0][0]["content"])
            self.assertEqual("world", entries[1]["output"]["generations"][0][0]["content"])
            self.assertIsNotNone(entries[1]["duration_ms"])


if __name__ == "__main__":
    unittest.main()
