"""搜索源基类与 HTTP 辅助方法。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from core.exceptions import SearchProviderAuthError, SearchProviderError
from search.schemas import SearchItem


class SearchProvider(ABC):
    name: str

    def __init__(self, *, enabled: bool = True, timeout_seconds: float = 8):
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return self.enabled

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[SearchItem]:
        raise NotImplementedError

    def _request_json(
        self,
        request: urllib.request.Request,
    ) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise SearchProviderAuthError(f"{self.name} 认证或权限失败") from exc
            raise SearchProviderError(f"{self.name} HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SearchProviderError(f"{self.name} 网络请求失败: {exc}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SearchProviderError(f"{self.name} 返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise SearchProviderError(f"{self.name} 返回结构不正确")
        return payload

