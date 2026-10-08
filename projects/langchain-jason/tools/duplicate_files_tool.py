"""重复文件检测工具。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from core.schemas import ToolResult
from services.directory_scanner import FileRecord, InventoryStore
from tools.directory_common import failed, relative_name


class DuplicateFilesTool:
    name = "find_duplicate_files"

    def __init__(self, inventories: InventoryStore, max_hash_file_bytes: int):
        self.inventories = inventories
        self.max_hash_file_bytes = max_hash_file_bytes

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def invoke(self, directory: str) -> ToolResult:
        try:
            snapshot = self.inventories.get(directory)
            by_size: dict[int, list[FileRecord]] = defaultdict(list)
            for item in snapshot.files:
                if 0 < item.size <= self.max_hash_file_bytes:
                    by_size[item.size].append(item)

            by_hash: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
            warnings = list(snapshot.errors[:20])
            for size, records in by_size.items():
                if len(records) < 2:
                    continue
                for record in records:
                    try:
                        by_hash[(size, self._sha256(record.path))].append(record)
                    except OSError as exc:
                        warnings.append(f"{record.path.name}: {exc.strerror or exc}")

            groups = []
            wasted_bytes = 0
            for (size, digest), records in by_hash.items():
                if len(records) < 2:
                    continue
                wasted_bytes += size * (len(records) - 1)
                groups.append(
                    {
                        "sha256": digest,
                        "size_bytes": size,
                        "copies": len(records),
                        "files": [relative_name(item.path, snapshot.root) for item in records],
                    }
                )
            groups.sort(key=lambda item: (-item["size_bytes"], item["sha256"]))
            skipped_large = sum(1 for item in snapshot.files if item.size > self.max_hash_file_bytes)
            return ToolResult(
                ok=True,
                tool=self.name,
                data={
                    "directory": snapshot.root.name,
                    "duplicate_group_count": len(groups),
                    "estimated_reclaimable_bytes": wasted_bytes,
                    "skipped_large_files": skipped_large,
                    "groups": groups,
                    "truncated": snapshot.truncated,
                },
                warnings=warnings,
            )
        except Exception as exc:
            return failed(self.name, exc)

