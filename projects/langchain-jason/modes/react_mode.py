"""由大模型自主选择工具的 ReAct 模式。"""

from __future__ import annotations

import time

from agent.agent_factory import create_react_agent
from agent.state import InMemorySessionStore
from tools.registry import ToolRegistry
from agent.trace_callback import AgentTraceCallback
from core.config_loader import Settings
from core.exceptions import AppError
from core.prompt_registry import PromptRegistry
from core.schemas import ExecutionRequest
from core.tracing import TraceRecorder


class ReactMode:
    """在同一实例中复用 Agent，并按会话保存最近的对话消息。"""

    name = "react"

    def __init__(
        self,
        settings: Settings,
        prompts: PromptRegistry,
        registry: ToolRegistry,
        sessions: InMemorySessionStore | None = None,
        recorder: TraceRecorder | None = None,
    ):
        self.settings = settings
        self.prompts = prompts
        self.registry = registry
        self.sessions = sessions or InMemorySessionStore()
        self.recorder = recorder
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_react_agent(self.settings, self.prompts, self.registry)
        return self._agent

    def prepare(self) -> None:
        """启动交互会话前检查密钥并完成 Agent 装配。"""
        self._get_agent()

    def run(self, request: ExecutionRequest) -> dict:
        prompt = request.prompt or request.query
        if not prompt:
            raise AppError("ReAct 模式需要 --prompt 参数。")
        state = self.sessions.get(request.session_id)
        if request.directory:
            state.current_directory = request.directory
            prompt = f"{prompt}\n当前用户指定目录: {request.directory}"
        messages = [*state.messages, {"role": "user", "content": prompt}]
        started = time.perf_counter()
        config = {"callbacks": [AgentTraceCallback(self.recorder)]} if self.recorder else None
        try:
            response = self._get_agent().invoke({"messages": messages}, config=config)
        except Exception as exc:
            if self.recorder:
                self.recorder.record(
                    event="agent.failed",
                    component="react",
                    status="error",
                    input_data={"messages": messages},
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
            raise
        content = response["messages"][-1].content
        self.sessions.remember(request.session_id, "user", prompt)
        self.sessions.remember(request.session_id, "assistant", str(content))
        if self.recorder:
            self.recorder.record(
                event="agent.completed",
                component="react",
                status="success",
                input_data={"messages": messages},
                output_data={"answer": content},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        return {"mode": self.name, "task": request.task, "result": {"answer": content}}
