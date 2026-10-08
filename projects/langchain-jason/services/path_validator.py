"""本地目录安全校验。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.exceptions import PathValidationError


class PathValidator:
    def __init__(self, allowed_roots: Iterable[str], reject_exact_paths: Iterable[str]):
        self.allowed_roots = [Path(item).expanduser().resolve() for item in allowed_roots]
        self.rejected = {Path(item).expanduser().resolve() for item in reject_exact_paths}
        if not self.allowed_roots:
            raise PathValidationError("必须至少配置一个允许的根目录。")

    def validate_directory(self, raw_path: str) -> Path:
        if not raw_path or not raw_path.strip():
            raise PathValidationError("目录路径不能为空。")
        candidate = Path(raw_path).expanduser().resolve(strict=True)
        if candidate in self.rejected:
            raise PathValidationError(f"禁止直接扫描高风险路径: {candidate}")
        if not candidate.is_dir():
            raise PathValidationError(f"路径不是目录: {candidate}")
        if not any(candidate == root or candidate.is_relative_to(root) for root in self.allowed_roots):
            roots = ", ".join(str(item) for item in self.allowed_roots)
            raise PathValidationError(f"目录不在允许范围内，允许的根目录: {roots}")
        return candidate

