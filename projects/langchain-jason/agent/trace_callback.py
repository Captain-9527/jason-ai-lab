"""LangChain Agent 模型与工具步骤追踪回调。"""

from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from core.tracing import TraceRecorder


class AgentTraceCallback(BaseCallbackHandler):
    def __init__(self, recorder: TraceRecorder):
        self.recorder = recorder
        self._started: dict[str, float] = {}
        self._components: dict[str, str] = {}
        self._lock = threading.Lock()

    def _start(self, run_id: UUID, component: str) -> None:
        with self._lock:
            self._started[str(run_id)] = time.perf_counter()
            self._components[str(run_id)] = component

    def _finish(self, run_id: UUID) -> tuple[float | None, str]:
        with self._lock:
            started = self._started.pop(str(run_id), None)
            component = self._components.pop(str(run_id), "unknown")
        duration = (time.perf_counter() - started) * 1000 if started is not None else None
        return duration, component

    @staticmethod
    def _messages(messages: list[list[Any]]) -> list[list[dict[str, Any]]]:
        return [
            [
                {
                    "type": getattr(message, "type", message.__class__.__name__),
                    "content": getattr(message, "content", str(message)),
                    "tool_calls": getattr(message, "tool_calls", None),
                }
                for message in batch
            ]
            for batch in messages
        ]

    @staticmethod
    def _generations(response: Any) -> list[list[dict[str, Any]]]:
        result = []
        for batch in getattr(response, "generations", []) or []:
            formatted = []
            for generation in batch:
                message = getattr(generation, "message", None)
                formatted.append(
                    {
                        "text": getattr(generation, "text", None),
                        "content": getattr(message, "content", None),
                        "tool_calls": getattr(message, "tool_calls", None),
                    }
                )
            result.append(formatted)
        return result

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        component = str(serialized.get("name") or serialized.get("id") or "chat_model")
        self._start(run_id, component)
        self.recorder.record(
            event="model.started",
            component=component,
            status="started",
            input_data={"messages": self._messages(messages)},
            metadata={"run_id": str(run_id)},
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        component = str(serialized.get("name") or serialized.get("id") or "llm")
        self._start(run_id, component)
        self.recorder.record(
            event="model.started",
            component=component,
            status="started",
            input_data={"prompts": prompts},
            metadata={"run_id": str(run_id)},
        )

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        duration, component = self._finish(run_id)
        usage = getattr(response, "llm_output", None)
        self.recorder.record(
            event="model.completed",
            component=component,
            status="success",
            output_data={"generations": self._generations(response)},
            duration_ms=duration,
            metadata={"run_id": str(run_id), "usage": usage},
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        duration, component = self._finish(run_id)
        self.recorder.record(
            event="model.failed",
            component=component,
            status="error",
            duration_ms=duration,
            error={"type": error.__class__.__name__, "message": str(error)},
            metadata={"run_id": str(run_id)},
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        component = str(serialized.get("name") or "tool")
        self._start(run_id, component)
        self.recorder.record(
            event="agent_tool.started",
            component=component,
            status="started",
            input_data=inputs if inputs is not None else input_str,
            metadata={"run_id": str(run_id)},
        )

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        duration, component = self._finish(run_id)
        self.recorder.record(
            event="agent_tool.completed",
            component=component,
            status="success",
            output_data=output,
            duration_ms=duration,
            metadata={"run_id": str(run_id)},
        )

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        duration, component = self._finish(run_id)
        self.recorder.record(
            event="agent_tool.failed",
            component=component,
            status="error",
            duration_ms=duration,
            error={"type": error.__class__.__name__, "message": str(error)},
            metadata={"run_id": str(run_id)},
        )
