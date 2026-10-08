"""规则路由、LCEL 与 ReAct 组合的混合模式。"""

from __future__ import annotations

import time

from chains.intent_chain import route_request
from core.schemas import ExecutionRequest
from modes.lcel_mode import LcelMode
from modes.react_mode import ReactMode
from core.tracing import TraceRecorder


class HybridMode:
    name = "hybrid"

    def __init__(
        self,
        lcel_mode: LcelMode,
        react_mode: ReactMode,
        recorder: TraceRecorder | None = None,
    ):
        self.lcel_mode = lcel_mode
        self.react_mode = react_mode
        self.recorder = recorder

    def run(self, request: ExecutionRequest) -> dict:
        started = time.perf_counter()
        route, routed_task = route_request(request)
        if self.recorder:
            self.recorder.record(
                event="mode.routed",
                component="hybrid_router",
                status="success",
                input_data={
                    "task": request.task,
                    "directory": request.directory,
                    "query": request.query,
                    "prompt": request.prompt,
                },
                output_data={"route": route, "routed_task": routed_task},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        routed = ExecutionRequest(
            task=routed_task,
            directory=request.directory,
            query=request.query,
            prompt=request.prompt,
            session_id=request.session_id,
        )
        if route in {"direct", "lcel"}:
            payload = self.lcel_mode.run(routed)
        else:
            payload = self.react_mode.run(routed)
        return {
            "mode": self.name,
            "task": routed_task,
            "route": route,
            "executor": payload["mode"],
            "result": payload["result"],
        }
