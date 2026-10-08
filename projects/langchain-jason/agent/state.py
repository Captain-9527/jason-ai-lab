"""轻量级会话短期记忆。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass(slots=True)
class SessionState:
    current_directory: str | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemorySessionStore:
    """进程内记忆；重启后不保留。"""

    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages
        self._sessions: dict[str, SessionState] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            return self._sessions.setdefault(session_id, SessionState())

    def remember(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            state = self._sessions.setdefault(session_id, SessionState())
            state.messages.append({"role": role, "content": content})
            state.messages = state.messages[-self.max_messages :]

