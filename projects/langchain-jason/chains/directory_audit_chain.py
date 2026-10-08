"""目录审计 LCEL 并行链。"""

from __future__ import annotations

from tools.registry import ToolRegistry
from core.exceptions import ConfigurationError


def build_directory_audit_chain(registry: ToolRegistry):
    try:
        from langchain_core.runnables import RunnableLambda, RunnableParallel
    except ImportError as exc:
        raise ConfigurationError("缺少 langchain-core，无法创建 LCEL 任务链。") from exc

    def call(name: str):
        return RunnableLambda(
            lambda payload: registry.invoke(name, directory=payload["directory"]).to_dict()
        )

    return RunnableParallel(
        count=call("count_directory_files"),
        distribution=call("analyze_file_distribution"),
        duplicates=call("find_duplicate_files"),
        activity=call("analyze_file_activity"),
    )
