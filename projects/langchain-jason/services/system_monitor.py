"""不读取进程命令行或用户内容的系统健康摘要。"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import time
from typing import Any


class SystemMonitor:
    def __init__(self, command_timeout_seconds: float = 3):
        self.command_timeout_seconds = command_timeout_seconds
        self._warnings: list[str] = []

    def _run(self, args: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._warnings.append(f"{args[0]} 调用失败: {exc}")
            return None
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"退出码 {completed.returncode}"
            self._warnings.append(f"{' '.join(args[:2])} 不可用: {detail}")
            return None
        return completed.stdout.strip()

    def _memory(self) -> dict[str, Any]:
        total_raw = self._run(["sysctl", "-n", "hw.memsize"])
        vm_raw = self._run(["vm_stat"])
        result: dict[str, Any] = {}
        if total_raw and total_raw.isdigit():
            result["total_bytes"] = int(total_raw)
        if vm_raw:
            page_match = re.search(r"page size of (\d+) bytes", vm_raw)
            page_size = int(page_match.group(1)) if page_match else 4096
            pages: dict[str, int] = {}
            for line in vm_raw.splitlines():
                match = re.match(r"([^:]+):\s+(\d+)\.", line)
                if match:
                    pages[match.group(1)] = int(match.group(2))
            available_pages = sum(
                pages.get(name, 0)
                for name in ("Pages free", "Pages inactive", "Pages speculative")
            )
            result["available_bytes_estimate"] = available_pages * page_size
        return result

    def _uptime_seconds(self) -> int | None:
        raw = self._run(["sysctl", "-n", "kern.boottime"])
        match = re.search(r"sec\s*=\s*(\d+)", raw or "")
        return max(0, int(time.time()) - int(match.group(1))) if match else None

    def inspect(self) -> dict[str, Any]:
        self._warnings = []
        disk = shutil.disk_usage("/")
        try:
            load_average = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            load_average = []
        battery = self._run(["pmset", "-g", "batt"])
        interfaces = [name for _, name in socket.if_nameindex()]
        result = {
            "platform": {
                "system": platform.system(),
                "release": platform.mac_ver()[0] or platform.release(),
                "architecture": platform.machine(),
                "uptime_seconds": self._uptime_seconds(),
            },
            "cpu": {
                "logical_cores": os.cpu_count(),
                "load_average_1_5_15": load_average,
            },
            "memory": self._memory(),
            "disk": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "used_percent": round(disk.used / disk.total * 100, 2) if disk.total else None,
            },
            "battery_summary": battery,
            "network_interfaces": interfaces,
        }
        result["_warnings"] = self._warnings
        return result
