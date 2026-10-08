"""LangChain ReAct/Tool-calling Agent 装配。"""

from __future__ import annotations

import json
import os
from typing import Any

from tools.registry import ToolRegistry
from core.config_loader import Settings
from core.exceptions import ConfigurationError
from core.prompt_registry import PromptRegistry


def _json_result(result: Any) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)


def create_react_agent(
    settings: Settings,
    prompts: PromptRegistry,
    registry: ToolRegistry,
):
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ConfigurationError("ReAct 模式需要环境变量 DASHSCOPE_API_KEY。")
    try:
        from langchain.agents import create_agent
        from langchain_community.chat_models import ChatTongyi
        from langchain_core.tools import StructuredTool
    except ImportError as exc:
        raise ConfigurationError("缺少 LangChain 依赖，请先安装 requirements.txt。") from exc

    def count_directory_files(directory: str) -> str:
        """统计指定目录的文件数、目录数和总容量。"""
        return _json_result(registry.invoke("count_directory_files", directory=directory))

    def analyze_file_distribution(directory: str) -> str:
        """按扩展名分析指定目录的文件数量和容量分布。"""
        return _json_result(registry.invoke("analyze_file_distribution", directory=directory))

    def find_duplicate_files(directory: str) -> str:
        """使用 SHA-256 查找指定目录中内容完全相同的文件。"""
        return _json_result(registry.invoke("find_duplicate_files", directory=directory))

    def analyze_file_activity(directory: str) -> str:
        """分析指定目录中近期、长期未修改和大文件。"""
        return _json_result(registry.invoke("analyze_file_activity", directory=directory))

    def web_search(query: str, max_results: int = 5) -> str:
        """搜索公开网络信息，内部会按配置在多搜索源间降级。"""
        return _json_result(
            registry.invoke("web_search", query=query, max_results=max_results)
        )

    def inspect_mac_system_health() -> str:
        """只读检查 macOS 的 CPU、内存、磁盘、电池和网络接口摘要。"""
        return _json_result(registry.invoke("inspect_mac_system_health"))

    functions = [
        count_directory_files,
        analyze_file_distribution,
        find_duplicate_files,
        analyze_file_activity,
        web_search,
        inspect_mac_system_health,
    ]
    tools = [
        StructuredTool.from_function(
            func=function,
            name=function.__name__,
            description=registry.description(function.__name__),
        )
        for function in functions
    ]
    model = ChatTongyi(
        model=settings.get("model.name", "qwen-turbo"),
        dashscope_api_key=api_key,
        temperature=float(settings.get("model.temperature", 0)),
    )
    return create_agent(model, tools, system_prompt=prompts.get("system.react"))
