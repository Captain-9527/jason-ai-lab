"""系统健康检查工具。"""

from __future__ import annotations

from core.schemas import ToolResult
from services.system_monitor import SystemMonitor
from tools.directory_common import failed


class SystemHealthTool:
    name = "inspect_mac_system_health"

    def __init__(self, monitor: SystemMonitor):
        self.monitor = monitor

    def invoke(self) -> ToolResult:
        try:
            data = self.monitor.inspect()
            warnings = data.pop("_warnings", [])
            return ToolResult(ok=True, tool=self.name, data=data, warnings=warnings)
        except Exception as exc:
            return failed(self.name, exc)
