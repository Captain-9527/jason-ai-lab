"""文件类型与容量分布工具。"""

from __future__ import annotations

from collections import defaultdict

from core.schemas import ToolResult
from services.directory_scanner import InventoryStore
from tools.directory_common import failed


class FileDistributionTool:
    name = "analyze_file_distribution"

    def __init__(self, inventories: InventoryStore):
        self.inventories = inventories

    def invoke(self, directory: str) -> ToolResult:
        try:
            snapshot = self.inventories.get(directory)
            groups: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0})
            for item in snapshot.files:
                extension = item.path.suffix.lower() or "[no_extension]"
                groups[extension]["count"] += 1
                groups[extension]["bytes"] += item.size
            distribution = [
                {"extension": extension, **summary}
                for extension, summary in sorted(
                    groups.items(), key=lambda pair: (-pair[1]["bytes"], pair[0])
                )
            ]
            return ToolResult(
                ok=True,
                tool=self.name,
                data={
                    "directory": snapshot.root.name,
                    "type_count": len(distribution),
                    "distribution": distribution,
                    "truncated": snapshot.truncated,
                },
                warnings=snapshot.errors[:20],
            )
        except Exception as exc:
            return failed(self.name, exc)

