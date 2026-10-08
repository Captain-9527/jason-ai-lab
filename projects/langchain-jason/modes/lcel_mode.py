"""固定、可预测的 LCEL 执行模式。"""

from __future__ import annotations

from tools.registry import ToolRegistry
from chains.directory_audit_chain import build_directory_audit_chain
from core.exceptions import AppError, ConfigurationError
from core.schemas import ExecutionRequest


TASK_TO_TOOL = {
    "count": "count_directory_files",
    "distribution": "analyze_file_distribution",
    "duplicates": "find_duplicate_files",
    "activity": "analyze_file_activity",
    "search": "web_search",
    "system": "inspect_mac_system_health",
}


class LcelMode:
    name = "lcel"

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def _invoke_tool_chain(self, tool_name: str, payload: dict) -> dict:
        try:
            from langchain_core.runnables import RunnableLambda
        except ImportError as exc:
            raise ConfigurationError("缺少 langchain-core，无法执行 LCEL 任务。") from exc
        chain = RunnableLambda(
            lambda values: self.registry.invoke(tool_name, **values).to_dict()
        )
        return chain.invoke(payload)

    def run(self, request: ExecutionRequest) -> dict:
        task = request.task
        if task == "auto":
            task = "audit" if request.directory else "system"
        if task == "audit":
            if not request.directory:
                raise AppError("目录审计任务需要 --directory 参数。")
            result = build_directory_audit_chain(self.registry).invoke(
                {"directory": request.directory}
            )
            return {"mode": self.name, "task": task, "result": result}
        if task not in TASK_TO_TOOL:
            raise AppError(f"LCEL 模式不支持任务: {task}")

        tool_name = TASK_TO_TOOL[task]
        if task in {"count", "distribution", "duplicates", "activity"}:
            if not request.directory:
                raise AppError(f"{task} 任务需要 --directory 参数。")
            result = self._invoke_tool_chain(tool_name, {"directory": request.directory})
        elif task == "search":
            query = request.query or request.prompt
            if not query:
                raise AppError("search 任务需要 --query 或 --prompt 参数。")
            result = self._invoke_tool_chain(tool_name, {"query": query})
        else:
            result = self._invoke_tool_chain(tool_name, {})
        return {"mode": self.name, "task": task, "result": result}
