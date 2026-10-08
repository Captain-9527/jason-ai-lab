"""一次性命令的 Markdown 报告渲染。"""

from __future__ import annotations

import json
from typing import Any


def render_markdown_report(payload: dict[str, Any]) -> str:
    mode = payload.get("mode", "unknown")
    task = payload.get("task", "unknown")
    route = payload.get("route")
    lines = ["# 执行结果", "", f"- 模式：`{mode}`", f"- 任务：`{task}`"]
    if route:
        lines.append(f"- 路由：`{route}`")
    lines.extend(
        [
            "",
            "## 结果数据",
            "",
            "```json",
            json.dumps(payload.get("result"), ensure_ascii=False, indent=2, default=str),
            "```",
        ]
    )
    return "\n".join(lines)
