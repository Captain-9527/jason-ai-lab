"""对 Agent 暴露的单一搜索工具。"""

from __future__ import annotations

import re

from core.schemas import ToolResult
from search.gateway import SearchGateway
from tools.directory_common import failed


class WebSearchTool:
    name = "web_search"

    def __init__(self, gateway: SearchGateway, default_max_results: int = 5):
        self.gateway = gateway
        self.default_max_results = default_max_results

    def invoke(self, query: str, max_results: int | None = None) -> ToolResult:
        try:
            if re.search(r"(?:file://|/(?:Users|home|private|Volumes)/)", query):
                return ToolResult(
                    ok=False,
                    tool=self.name,
                    error="查询中包含本地绝对路径，为避免泄露本地信息已拒绝联网检索。",
                )
            response = self.gateway.search(query, max_results or self.default_max_results)
            if not response.results:
                return ToolResult(
                    ok=False,
                    tool=self.name,
                    data=response.to_dict(),
                    error="所有可用搜索源均未返回结果。",
                )
            return ToolResult(ok=True, tool=self.name, data=response.to_dict())
        except Exception as exc:
            return failed(self.name, exc)
