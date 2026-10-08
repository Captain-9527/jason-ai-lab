"""文件活跃度和大文件分析工具。"""

from __future__ import annotations

import time

from core.schemas import ToolResult
from services.directory_scanner import InventoryStore
from tools.directory_common import failed, relative_name


class FileActivityTool:
    name = "analyze_file_activity"

    def __init__(
        self,
        inventories: InventoryStore,
        *,
        recent_days: int,
        stale_days: int,
        large_file_bytes: int,
    ):
        self.inventories = inventories
        self.recent_days = recent_days
        self.stale_days = stale_days
        self.large_file_bytes = large_file_bytes

    def invoke(self, directory: str) -> ToolResult:
        try:
            snapshot = self.inventories.get(directory)
            now = time.time()
            recent_cutoff = now - self.recent_days * 86400
            stale_cutoff = now - self.stale_days * 86400
            recent = [item for item in snapshot.files if item.modified_at >= recent_cutoff]
            stale = [item for item in snapshot.files if item.modified_at < stale_cutoff]
            large = sorted(
                (item for item in snapshot.files if item.size >= self.large_file_bytes),
                key=lambda item: item.size,
                reverse=True,
            )
            return ToolResult(
                ok=True,
                tool=self.name,
                data={
                    "directory": snapshot.root.name,
                    "recent_days": self.recent_days,
                    "recent_file_count": len(recent),
                    "stale_days": self.stale_days,
                    "stale_file_count": len(stale),
                    "large_file_threshold_bytes": self.large_file_bytes,
                    "large_files": [
                        {
                            "path": relative_name(item.path, snapshot.root),
                            "size_bytes": item.size,
                            "modified_at_epoch": round(item.modified_at, 3),
                        }
                        for item in large[:100]
                    ],
                    "truncated": snapshot.truncated,
                },
                warnings=snapshot.errors[:20],
            )
        except Exception as exc:
            return failed(self.name, exc)

