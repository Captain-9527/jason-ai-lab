"""统一提示词注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from core.exceptions import ConfigurationError


class PromptRegistry:
    def __init__(self, path: str | Path):
        prompt_path = Path(path)
        try:
            values = yaml.safe_load(prompt_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(f"无法读取提示词配置: {exc}") from exc
        if not isinstance(values, Mapping):
            raise ConfigurationError("提示词配置顶层必须是映射。")
        self._values = values

    def get(self, key: str, **variables: Any) -> str:
        current: Any = self._values
        for part in key.split("."):
            if not isinstance(current, Mapping) or part not in current:
                raise ConfigurationError(f"未找到提示词: {key}")
            current = current[part]
        if not isinstance(current, str):
            raise ConfigurationError(f"提示词不是文本: {key}")
        return current.format(**variables) if variables else current

