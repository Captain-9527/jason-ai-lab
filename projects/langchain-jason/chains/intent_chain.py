"""混合模式的可解释规则路由。"""

from __future__ import annotations

from core.schemas import ExecutionRequest


DIRECT_TASKS = {"count", "distribution", "duplicates", "activity", "search", "system"}


def route_request(request: ExecutionRequest) -> tuple[str, str]:
    if request.task == "audit":
        return "lcel", "audit"
    if request.task in DIRECT_TASKS:
        return "direct", request.task
    text = (request.prompt or "").lower()
    if any(token in text for token in ("系统健康", "cpu", "内存", "电池", "磁盘空间")):
        return "direct", "system"
    if any(token in text for token in ("搜索", "查一下", "联网", "search")):
        return "direct", "search"
    if any(token in text for token in ("重复文件", "duplicate")):
        return "direct", "duplicates"
    if request.directory and any(
        token in text for token in ("目录", "文件", "分析", "审计", "directory")
    ):
        return "lcel", "audit"
    return "react", "auto"

