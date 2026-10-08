"""目录工具的公共辅助方法。"""

from __future__ import annotations

from pathlib import Path

from core.schemas import ToolResult


def relative_name(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def failed(tool: str, exc: Exception) -> ToolResult:
    return ToolResult(ok=False, tool=tool, error=str(exc))

