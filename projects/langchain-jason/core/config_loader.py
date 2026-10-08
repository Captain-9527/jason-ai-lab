"""YAML 配置加载与环境变量覆盖。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from core.exceptions import ConfigurationError


ENV_OVERRIDES = {
    "LANGCHAIN_JASON_MODE": "app.default_mode",
    "LANGCHAIN_JASON_LOG_LEVEL": "logging.level",
    "LANGCHAIN_JASON_SEARCH_STRATEGY": "search.strategy",
}


class Settings:
    """提供点号路径访问的不可变视图。"""

    def __init__(self, values: Mapping[str, Any]):
        self._values = copy.deepcopy(dict(values))

    def get(self, dotted_path: str, default: Any = None) -> Any:
        current: Any = self._values
        for part in dotted_path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return copy.deepcopy(current)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)


def _set_nested(values: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    current = values
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def load_settings(path: str | Path, overrides: Mapping[str, Any] | None = None) -> Settings:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigurationError(f"配置文件不存在: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"无法读取配置: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("配置文件顶层必须是映射。")

    for env_name, dotted_path in ENV_OVERRIDES.items():
        if os.getenv(env_name):
            _set_nested(raw, dotted_path, os.environ[env_name])
    for dotted_path, value in (overrides or {}).items():
        if value is not None:
            _set_nested(raw, dotted_path, value)
    return Settings(raw)

