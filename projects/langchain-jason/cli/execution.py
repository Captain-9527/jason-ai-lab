"""一次性命令与持续对话共用的单轮执行和追踪。"""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any

from core.schemas import ExecutionRequest
from core.tracing import TraceRecorder


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _record_failure(
    recorder: TraceRecorder,
    request: ExecutionRequest,
    mode_name: str,
    started: float,
    error: BaseException,
    *,
    cancelled: bool = False,
) -> None:
    """失败也保留本轮 trace_id，便于与模型和工具步骤关联。"""
    recorder.record(
        event="task.cancelled" if cancelled else "task.failed",
        component="app",
        status="cancelled" if cancelled else "error",
        input_data={"task": request.task, "mode": mode_name},
        duration_ms=(time.perf_counter() - started) * 1000,
        error={
            "type": error.__class__.__name__,
            "message": str(error),
            "stack": traceback.format_exc(),
        },
    )


def execute_turn(mode: Any, recorder: TraceRecorder, request: ExecutionRequest) -> dict:
    """每轮新建 trace_id，同时保留调用方持有的 Agent 与会话状态。"""
    started = time.perf_counter()
    trace_id = recorder.start_trace(session_id=request.session_id, mode=mode.name)
    recorder.record(
        event="task.started",
        component="app",
        status="started",
        input_data={
            "task": request.task,
            "directory": request.directory,
            "query": request.query,
            "prompt": request.prompt,
            "session_id": request.session_id,
        },
    )
    try:
        result = mode.run(request)
    except KeyboardInterrupt as exc:
        _record_failure(recorder, request, mode.name, started, exc, cancelled=True)
        raise
    except Exception as exc:
        _record_failure(recorder, request, mode.name, started, exc)
        raise

    result["trace_id"] = trace_id
    result["trace_file"] = (
        str(recorder.path.relative_to(PROJECT_ROOT))
        if recorder.path.is_relative_to(PROJECT_ROOT)
        else str(recorder.path)
    )
    recorder.record(
        event="task.completed",
        component="app",
        status="success",
        input_data={"task": request.task, "mode": mode.name},
        output_data=result,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    return result
