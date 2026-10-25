"""每次运行一个 JSONL 事件文件，密钥脱敏，阶段输入输出默认记摘要。"""
import contextvars
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .storage import digest

CONTEXT = contextvars.ContextVar("trace_context", default={})


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in
                ("api_key", "authorization", "password", "secret", "access_token")) else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        key = os.getenv("DASHSCOPE_API_KEY")
        if key:
            value = value.replace(key, "[REDACTED]")
        value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
        return re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[IMAGE]", value)
    return value


class Trace:
    def __init__(self, path, payloads=False):
        self.path = Path(path)
        self.payloads = payloads
        self.lock = threading.Lock()

    def summary(self, value):
        safe = redact(value)
        return safe if self.payloads else {"sha256": digest(safe), "characters": len(json.dumps(safe, ensure_ascii=False))}

    def emit(self, event, **data):
        item = redact({"time": datetime.now(timezone.utc).isoformat(), "event": event,
                       **CONTEXT.get(), **data})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            f.flush()

    @contextmanager
    def scope(self, **values):
        token = CONTEXT.set({**CONTEXT.get(), **values})
        try:
            yield
        finally:
            CONTEXT.reset(token)

    @contextmanager
    def stage(self, name, inputs=None):
        start = time.monotonic()
        out = {}
        self.emit("stage_start", stage=name, inputs=self.summary(inputs))
        try:
            yield out
        except Exception as exc:
            self.emit("stage_error", stage=name, error_type=type(exc).__name__,
                      error=str(exc)[:1000], duration_seconds=time.monotonic() - start)
            raise
        else:
            self.emit("stage_end", stage=name, outputs=self.summary(out),
                      duration_seconds=time.monotonic() - start)
