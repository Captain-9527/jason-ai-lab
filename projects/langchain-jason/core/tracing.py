"""单文件 JSONL 执行追踪。"""

from __future__ import annotations

import contextvars
import dataclasses
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.config_loader import Settings
from core.exceptions import ConfigurationError, TraceWriteError


_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)
_SESSION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_session_id", default=None
)
_MODE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_mode", default=None
)

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|password|passwd|secret|authorization|cookie)", re.IGNORECASE
)
_LOCAL_PATH = re.compile(
    r"(?<![\w.-])/(?:Users|home|private|Volumes)/(?:[^\s\"'`]+)"
)


class TraceRecorder:
    """将所有追踪事件追加到同一个 JSONL 文件。"""

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        payload_mode: str = "sanitized",
        max_payload_chars: int = 12000,
        max_collection_items: int = 100,
    ):
        if payload_mode not in {"sanitized", "none"}:
            raise ConfigurationError("tracing.payload_mode 仅支持 sanitized 或 none。")
        self.path = path
        self.enabled = enabled
        self.payload_mode = payload_mode
        self.max_payload_chars = max_payload_chars
        self.max_collection_items = max_collection_items
        self._write_lock = threading.Lock()
        self._fallback_trace_id: str | None = None
        self._fallback_session_id: str | None = None
        self._fallback_mode: str | None = None
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                os.close(descriptor)
            except OSError as exc:
                raise TraceWriteError(f"无法初始化追踪文件: {exc}") from exc

    @property
    def trace_id(self) -> str | None:
        return _TRACE_ID.get() or self._fallback_trace_id

    def start_trace(self, *, session_id: str, mode: str) -> str:
        trace_id = uuid4().hex
        _TRACE_ID.set(trace_id)
        _SESSION_ID.set(session_id)
        _MODE.set(mode)
        self._fallback_trace_id = trace_id
        self._fallback_session_id = session_id
        self._fallback_mode = mode
        return trace_id

    def clear_context(self) -> None:
        _TRACE_ID.set(None)
        _SESSION_ID.set(None)
        _MODE.set(None)
        self._fallback_trace_id = None
        self._fallback_session_id = None
        self._fallback_mode = None

    @staticmethod
    def _mask_path(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,;:)]}")
        suffix = match.group(0)[len(raw) :]
        name = Path(raw).name
        return f"<local_path>/{name}{suffix}" if name else f"<local_path>{suffix}"

    def _sanitize(self, value: Any, *, key: str | None = None) -> Any:
        if key and _SENSITIVE_KEY.search(key):
            return "<redacted>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, Path):
            return f"<local_path>/{value.name}"
        if dataclasses.is_dataclass(value):
            return self._sanitize(dataclasses.asdict(value))
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if isinstance(value, str):
            masked = _LOCAL_PATH.sub(self._mask_path, value)
            if len(masked) > self.max_payload_chars:
                return masked[: self.max_payload_chars] + "<truncated>"
            return masked
        if isinstance(value, dict):
            items = list(value.items())[: self.max_collection_items]
            result = {
                str(item_key): self._sanitize(item_value, key=str(item_key))
                for item_key, item_value in items
            }
            if len(value) > self.max_collection_items:
                result["_truncated_items"] = len(value) - self.max_collection_items
            return result
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            result = [self._sanitize(item) for item in values[: self.max_collection_items]]
            if len(values) > self.max_collection_items:
                result.append({"_truncated_items": len(values) - self.max_collection_items})
            return result
        if hasattr(value, "model_dump"):
            try:
                return self._sanitize(value.model_dump())
            except Exception:
                pass
        return self._sanitize(str(value))

    def _payload(self, value: Any) -> Any:
        if self.payload_mode == "none" or value is None:
            return None
        sanitized = self._sanitize(value)
        encoded = json.dumps(sanitized, ensure_ascii=False, default=str)
        if len(encoded) <= self.max_payload_chars:
            return sanitized
        return {
            "truncated": True,
            "original_chars": len(encoded),
            "preview": encoded[: self.max_payload_chars],
        }

    def record(
        self,
        *,
        event: str,
        component: str,
        status: str,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: float | None = None,
        error: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        entry = {
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "trace_id": self.trace_id or uuid4().hex,
            "session_id": _SESSION_ID.get() or self._fallback_session_id,
            "mode": _MODE.get() or self._fallback_mode,
            "event": event,
            "component": component,
            "status": status,
            "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
            "input": self._payload(input_data),
            "output": self._payload(output_data),
            "error": self._payload(error),
            "metadata": self._payload(metadata),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        try:
            with self._write_lock:
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    try:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX)
                    except ImportError:
                        pass
                    os.write(descriptor, line.encode("utf-8"))
                finally:
                    os.close(descriptor)
        except OSError as exc:
            raise TraceWriteError(f"无法写入追踪文件: {exc}") from exc


def build_trace_recorder(settings: Settings, project_root: Path) -> TraceRecorder:
    configured_path = Path(settings.get("tracing.file", "logs/execution_trace.jsonl"))
    trace_path = (
        configured_path.expanduser().resolve()
        if configured_path.is_absolute()
        else (project_root / configured_path).resolve()
    )
    project_root = project_root.resolve()
    if not trace_path.is_relative_to(project_root):
        raise ConfigurationError("追踪文件必须位于项目目录内。")
    return TraceRecorder(
        trace_path,
        enabled=bool(settings.get("tracing.enabled", True)),
        payload_mode=str(settings.get("tracing.payload_mode", "sanitized")),
        max_payload_chars=int(settings.get("tracing.max_payload_chars", 12000)),
        max_collection_items=int(settings.get("tracing.max_collection_items", 100)),
    )

