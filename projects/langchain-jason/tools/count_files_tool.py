"""文件数量统计工具。"""

from __future__ import annotations

from core.schemas import ToolResult
from services.directory_scanner import InventoryStore
from tools.directory_common import failed


class CountDirectoryFilesTool:
    name = "count_directory_files"

    def __init__(self, inventories: InventoryStore):
        self.inventories = inventories

    def invoke(self, directory: str) -> ToolResult:
        try:
            snapshot = self.inventories.get(directory)
            return ToolResult(
                ok=True,
                tool=self.name,
                data={
                    "directory": snapshot.root.name,
                    "file_count": len(snapshot.files),
                    "directory_count": snapshot.directory_count,
                    "total_bytes": sum(item.size for item in snapshot.files),
                    "skipped_symlinks": snapshot.skipped_symlinks,
                    "truncated": snapshot.truncated,
                    "duration_seconds": snapshot.duration_seconds,
                },
                warnings=snapshot.errors[:20],
            )
        except Exception as exc:
            return failed(self.name, exc)

