"""搜索结果标准化结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class SearchItem:
    title: str
    url: str
    snippet: str
    source: str


@dataclass(slots=True)
class SearchAttempt:
    provider: str
    status: str
    detail: str | None = None
    result_count: int = 0
    duration_ms: float | None = None


@dataclass(slots=True)
class SearchResponse:
    query: str
    strategy: str
    provider_used: list[str] = field(default_factory=list)
    degraded: bool = False
    attempts: list[SearchAttempt] = field(default_factory=list)
    results: list[SearchItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
