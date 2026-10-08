"""DuckDuckGo Instant Answer 免密钥搜索源。"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

from search.providers.base import SearchProvider
from search.schemas import SearchItem


class DuckDuckGoProvider(SearchProvider):
    name = "duckduckgo"

    def _flatten(self, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for item in topics:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("Topics"), list):
                flattened.extend(self._flatten(item["Topics"]))
            elif item.get("FirstURL"):
                flattened.append(item)
        return flattened

    def search(self, query: str, max_results: int) -> list[SearchItem]:
        params = urllib.parse.urlencode(
            {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        )
        request = urllib.request.Request(
            f"https://api.duckduckgo.com/?{params}",
            headers={"User-Agent": "LangChain-Jason/1.0"},
        )
        payload = self._request_json(request)
        topics = self._flatten(payload.get("RelatedTopics", []))
        results = [
            SearchItem(
                title=str(item.get("Text", "")).split(" - ", 1)[0],
                url=str(item.get("FirstURL", "")),
                snippet=str(item.get("Text", "")),
                source=self.name,
            )
            for item in topics[:max_results]
        ]
        if payload.get("AbstractURL") and len(results) < max_results:
            results.insert(
                0,
                SearchItem(
                    title=str(payload.get("Heading", query)),
                    url=str(payload["AbstractURL"]),
                    snippet=str(payload.get("AbstractText", "")),
                    source=self.name,
                ),
            )
        return results[:max_results]
