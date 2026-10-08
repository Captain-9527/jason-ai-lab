"""Tavily 搜索源。"""

from __future__ import annotations

import json
import urllib.request

from search.providers.base import SearchProvider
from search.schemas import SearchItem


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str | None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return super().available and bool(self.api_key)

    def search(self, query: str, max_results: int) -> list[SearchItem]:
        body = json.dumps(
            {"api_key": self.api_key, "query": query, "max_results": max_results}
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "LangChain-Jason/1.0"},
        )
        payload = self._request_json(request)
        return [
            SearchItem(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
                source=self.name,
            )
            for item in payload.get("results", [])[:max_results]
            if item.get("url")
        ]

