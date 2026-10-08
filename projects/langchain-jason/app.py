"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from cli.console import run_console
from cli.execution import execute_turn
from cli.report import render_markdown_report
from core.config_loader import load_settings
from core.exceptions import ConfigurationError
from core.prompt_registry import PromptRegistry
from core.schemas import ExecutionRequest
from core.tracing import TraceRecorder, build_trace_recorder
from modes.mode_factory import create_mode
from tools.registry import build_tool_registry


PROJECT_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读的 macOS 目录分析、外部检索与系统健康 Agent"
    )
    parser.add_argument("--mode", choices=("lcel", "react", "hybrid"))
    parser.add_argument(
        "--task",
        default="auto",
        choices=("auto", "audit", "count", "distribution", "duplicates", "activity", "search", "system"),
    )
    parser.add_argument("--directory", help="待分析目录，必须位于允许根目录下")
    parser.add_argument("--query", help="联网检索关键词")
    parser.add_argument("--prompt", help="Agent 自然语言任务")
    parser.add_argument("--session-id", default="default", help="进程内短期记忆会话 ID")
    parser.add_argument("--interactive", action="store_true", help="启动 ReAct 多轮终端对话")
    parser.add_argument("--json", action="store_true", help="输出 JSON，默认输出 Markdown")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "settings.yaml"),
        help="运行配置路径",
    )
    parser.add_argument(
        "--prompts",
        default=str(PROJECT_ROOT / "config" / "prompts.yaml"),
        help="提示词配置路径",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    recorder: TraceRecorder | None = None
    try:
        if args.interactive and args.mode not in (None, "react"):
            raise ConfigurationError("交互模式仅支持 ReAct；请使用 --mode react 或省略 --mode。")
        if args.interactive and args.task != "auto":
            raise ConfigurationError("交互模式不支持固定 --task；请在对话中描述任务。")
        if args.interactive and args.query:
            raise ConfigurationError("交互模式请使用 --prompt 设置首轮问题，不支持 --query。")
        selected_mode = "react" if args.interactive else args.mode
        settings = load_settings(args.config, {"app.default_mode": selected_mode})
        logging.basicConfig(
            level=getattr(logging, str(settings.get("logging.level", "INFO")).upper(), logging.INFO),
            format="%(levelname)s %(name)s: %(message)s",
        )
        prompts = PromptRegistry(args.prompts)
        mode_name = settings.get("app.default_mode", "hybrid")
        recorder = build_trace_recorder(settings, PROJECT_ROOT)
        registry = build_tool_registry(settings, prompts, recorder)
        mode = create_mode(mode_name, settings, prompts, registry, recorder)
        if args.interactive:
            return run_console(
                mode,
                recorder,
                session_id=args.session_id,
                directory=args.directory,
                initial_prompt=args.prompt,
                json_output=args.json,
            )
        request = ExecutionRequest(
            task=args.task,
            directory=args.directory,
            query=args.query,
            prompt=args.prompt,
            session_id=args.session_id,
        )
        result = execute_turn(mode, recorder, request)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(render_markdown_report(result))
        return 0
    except Exception as exc:
        print(f"执行失败: {exc}", file=sys.stderr)
        return 2
    finally:
        if recorder:
            recorder.clear_context()


if __name__ == "__main__":
    raise SystemExit(main())
