"""可限流、可复用的目录扫描器。"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from services.path_validator import PathValidator


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: Path
    size: int
    modified_at: float


@dataclass(slots=True)
class ScanResult:
    root: Path
    files: list[FileRecord] = field(default_factory=list)
    directory_count: int = 0
    skipped_symlinks: int = 0
    errors: list[str] = field(default_factory=list)
    truncated: bool = False
    duration_seconds: float = 0.0


class DirectoryScanner:
    def __init__(
        self,
        validator: PathValidator,
        *,
        max_depth: int,
        max_files: int,
        timeout_seconds: float,
        include_hidden: bool,
    ):
        self.validator = validator
        self.max_depth = max_depth
        self.max_files = max_files
        self.timeout_seconds = timeout_seconds
        self.include_hidden = include_hidden

    def scan(self, directory: str) -> ScanResult:
        root = self.validator.validate_directory(directory)
        started = time.monotonic()
        result = ScanResult(root=root)
        stack: list[tuple[Path, int]] = [(root, 0)]

        while stack:
            if time.monotonic() - started > self.timeout_seconds:
                result.truncated = True
                result.errors.append("扫描超时，已返回部分结果。")
                break
            current, depth = stack.pop()
            result.directory_count += 1
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if not self.include_hidden and entry.name.startswith("."):
                            continue
                        if entry.is_symlink():
                            result.skipped_symlinks += 1
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if depth < self.max_depth:
                                    stack.append((Path(entry.path), depth + 1))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            stat = entry.stat(follow_symlinks=False)
                            result.files.append(
                                FileRecord(Path(entry.path), stat.st_size, stat.st_mtime)
                            )
                            if len(result.files) >= self.max_files:
                                result.truncated = True
                                result.errors.append("已达最大文件数限制。")
                                stack.clear()
                                break
                        except OSError as exc:
                            result.errors.append(f"{entry.name}: {exc.strerror or exc}")
            except OSError as exc:
                result.errors.append(f"{current.name or current}: {exc.strerror or exc}")
        result.duration_seconds = round(time.monotonic() - started, 4)
        return result


class InventoryStore:
    """在一次复合任务中复用目录快照，避免重复扫描。"""

    def __init__(self, scanner: DirectoryScanner, cache_seconds: float):
        self.scanner = scanner
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, ScanResult]] = {}
        self._lock = threading.Lock()

    def get(self, directory: str) -> ScanResult:
        with self._lock:
            root = str(self.scanner.validator.validate_directory(directory))
            cached = self._cache.get(root)
            if cached and time.monotonic() - cached[0] <= self.cache_seconds:
                return cached[1]
            result = self.scanner.scan(root)
            self._cache[root] = (time.monotonic(), result)
            return result

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

