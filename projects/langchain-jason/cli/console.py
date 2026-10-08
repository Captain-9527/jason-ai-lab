"""持续运行的终端对话循环。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from cli.execution import execute_turn
from core.exceptions import TraceWriteError
from core.schemas import ExecutionRequest
from core.tracing import TraceRecorder
from modes.react_mode import ReactMode


EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", "退出"}


def run_console(
    mode: ReactMode,
    recorder: TraceRecorder,
    *,
    session_id: str,
    directory: str | None = None,
    initial_prompt: str | None = None,
    json_output: bool = False,
    input_reader: Callable[[str], str] | None = None,
) -> int:
    """复用同一 Agent 实例处理多轮输入；退出后进程内记忆自然释放。"""
    mode.prepare()
    read = input_reader or input
    pending_prompt = initial_prompt
    pending_directory = directory
    print("对话已启动。输入 退出、exit 或 quit 结束；也可按 Ctrl+D。")

    while True:
        if pending_prompt is not None:
            message = pending_prompt.strip()
            pending_prompt = None
            if message:
                print(f"你> {message}")
        else:
            try:
                message = read("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n对话已结束。")
                return 0

        if not message:
            continue
        if message.lower() in EXIT_COMMANDS:
            print("对话已结束。")
            return 0

        request = ExecutionRequest(
            task="auto",
            directory=pending_directory,
            prompt=message,
            session_id=session_id,
        )
        try:
            result = execute_turn(mode, recorder, request)
        except KeyboardInterrupt:
            print("\n对话已结束。")
            return 0
        except Exception as exc:
            print(f"本轮执行失败: {exc}", file=sys.stderr)
            if isinstance(exc, TraceWriteError):
                return 2
            continue

        # 目录只需放入首轮输入；后续问题通过同一会话的历史消息获得上下文。
        pending_directory = None
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"助手> {result['result']['answer']}")
            print(f"trace_id: {result['trace_id']}")

