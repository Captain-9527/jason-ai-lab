"""六个领域工具的装配、名称注册与统一调用入口。"""

from __future__ import annotations

import os
import time
from typing import Any

from core.config_loader import Settings
from core.prompt_registry import PromptRegistry
from core.schemas import ToolResult
from core.tracing import TraceRecorder
from search.gateway import SearchGateway
from search.providers.duckduckgo_provider import DuckDuckGoProvider
from search.providers.serpapi_provider import SerpApiProvider
from search.providers.tavily_provider import TavilyProvider
from services.directory_scanner import DirectoryScanner, InventoryStore
from services.path_validator import PathValidator
from services.system_monitor import SystemMonitor
from tools.count_files_tool import CountDirectoryFilesTool
from tools.duplicate_files_tool import DuplicateFilesTool
from tools.file_activity_tool import FileActivityTool
from tools.file_distribution_tool import FileDistributionTool
from tools.system_health_tool import SystemHealthTool
from tools.web_search_tool import WebSearchTool


class ToolRegistry:
    """按工具名分发调用，并统一记录结果与耗时。"""

    def __init__(
        self,
        tools: list[Any],
        prompts: PromptRegistry,
        recorder: TraceRecorder | None = None,
    ):
        self._tools = {item.name: item for item in tools}
        self.prompts = prompts
        self.recorder = recorder

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def description(self, name: str) -> str:
        return self.prompts.get(f"tools.{name}")

    def invoke(self, name: str, **kwargs: Any) -> ToolResult:
        started = time.perf_counter()
        tool = self._tools.get(name)
        if tool is None:
            result = ToolResult(ok=False, tool=name, error=f"未知工具: {name}")
        else:
            try:
                result = tool.invoke(**kwargs)
            except Exception as exc:
                result = ToolResult(
                    ok=False,
                    tool=name,
                    error=f"工具未处理异常: {exc}",
                )
        if self.recorder:
            self.recorder.record(
                event="tool.completed" if result.ok else "tool.failed",
                component=name,
                status="success" if result.ok else "error",
                input_data=kwargs,
                output_data=result.to_dict(),
                duration_ms=(time.perf_counter() - started) * 1000,
                error=result.error,
            )
        return result


def _build_search_gateway(
    settings: Settings,
    recorder: TraceRecorder | None = None,
) -> SearchGateway:
    """根据配置创建可降级的搜索源链；密钥只从进程环境读取。"""
    providers = []
    for item in settings.get("search.providers", []):
        name = item.get("name")
        common = {
            "enabled": bool(item.get("enabled", True)),
            "timeout_seconds": float(item.get("timeout_seconds", 8)),
        }
        api_key = os.getenv(item.get("api_key_env", "")) if item.get("api_key_env") else None
        if name == "serpapi":
            providers.append(SerpApiProvider(api_key=api_key, **common))
        elif name == "tavily":
            providers.append(TavilyProvider(api_key=api_key, **common))
        elif name == "duckduckgo":
            providers.append(DuckDuckGoProvider(**common))
    return SearchGateway(
        providers,
        strategy=settings.get("search.strategy", "sequential"),
        min_results=int(settings.get("search.min_results", 3)),
        recorder=recorder,
    )


def build_tool_registry(
    settings: Settings,
    prompts: PromptRegistry,
    recorder: TraceRecorder | None = None,
) -> ToolRegistry:
    """让目录工具共享一次扫描结果，并装配搜索与系统工具。"""
    validator = PathValidator(
        settings.get("directory_scan.allowed_roots", []),
        settings.get("directory_scan.reject_exact_paths", ["/", "~"]),
    )
    scanner = DirectoryScanner(
        validator,
        max_depth=int(settings.get("directory_scan.max_depth", 12)),
        max_files=int(settings.get("directory_scan.max_files", 200000)),
        timeout_seconds=float(settings.get("directory_scan.timeout_seconds", 30)),
        include_hidden=bool(settings.get("directory_scan.include_hidden", False)),
    )
    inventories = InventoryStore(
        scanner,
        cache_seconds=float(settings.get("directory_scan.cache_seconds", 60)),
    )
    tools = [
        CountDirectoryFilesTool(inventories),
        FileDistributionTool(inventories),
        DuplicateFilesTool(
            inventories,
            max_hash_file_bytes=int(settings.get("directory_scan.max_hash_file_mb", 1024))
            * 1024
            * 1024,
        ),
        FileActivityTool(
            inventories,
            recent_days=int(settings.get("directory_scan.recent_days", 30)),
            stale_days=int(settings.get("directory_scan.stale_days", 365)),
            large_file_bytes=int(settings.get("directory_scan.large_file_mb", 500))
            * 1024
            * 1024,
        ),
        WebSearchTool(
            _build_search_gateway(settings, recorder),
            default_max_results=int(settings.get("search.max_results", 5)),
        ),
        SystemHealthTool(
            SystemMonitor(float(settings.get("system_health.command_timeout_seconds", 3)))
        ),
    ]
    return ToolRegistry(tools, prompts, recorder)
