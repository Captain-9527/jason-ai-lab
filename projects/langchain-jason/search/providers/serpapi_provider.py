"""SerpAPI 搜索源。"""

from __future__ import annotations

import urllib.parse
import urllib.request

from search.providers.base import SearchProvider
from search.schemas import SearchItem


class SerpApiProvider(SearchProvider):
    name = "serpapi"

    def __init__(self, api_key: str | None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return super().available and bool(self.api_key)

    def search(self, query: str, max_results: int) -> list[SearchItem]:
        params = urllib.parse.urlencode(
            {"q": query, "api_key": self.api_key, "num": max_results, "engine": "google"}
        )
        request = urllib.request.Request(
            f"https://serpapi.com/search.json?{params}",
            headers={"User-Agent": "LangChain-Jason/1.0"},
        )
        payload = self._request_json(request)
        return [
            SearchItem(
                title=str(item.get("title", "")),
                url=str(item.get("link", "")),
                snippet=str(item.get("snippet", "")),
                source=self.name,
            )
            for item in payload.get("organic_results", [])[:max_results]
            if item.get("link")
        ]

