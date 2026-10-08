"""执行模式工厂。"""

from __future__ import annotations

from tools.registry import ToolRegistry
from core.config_loader import Settings
from core.exceptions import ConfigurationError
from core.prompt_registry import PromptRegistry
from modes.hybrid_mode import HybridMode
from modes.lcel_mode import LcelMode
from modes.react_mode import ReactMode
from core.tracing import TraceRecorder


def create_mode(
    mode_name: str,
    settings: Settings,
    prompts: PromptRegistry,
    registry: ToolRegistry,
    recorder: TraceRecorder | None = None,
):
    supported = settings.get("execution.supported_modes", ["react", "lcel", "hybrid"])
    if mode_name not in supported:
        raise ConfigurationError(f"不支持的处理模式: {mode_name}")
    lcel = LcelMode(registry)
    react = ReactMode(settings, prompts, registry, recorder=recorder)
    if mode_name == "lcel":
        return lcel
    if mode_name == "react":
        return react
    return HybridMode(lcel, react, recorder)
