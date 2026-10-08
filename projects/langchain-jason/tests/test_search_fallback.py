from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from core.exceptions import SearchProviderAuthError, SearchProviderError
from search.gateway import SearchGateway
from search.providers.base import SearchProvider
from search.schemas import SearchItem
from tools.web_search_tool import WebSearchTool
from core.tracing import TraceRecorder


class FakeProvider(SearchProvider):
    def __init__(self, name: str, result=None, error: Exception | None = None):
        super().__init__()
        self.name = name
        self.result = result or []
        self.error = error

    def search(self, query: str, max_results: int) -> list[SearchItem]:
        if self.error:
            raise self.error
        return self.result[:max_results]


def item(source: str, index: int) -> SearchItem:
    return SearchItem(f"title-{index}", f"https://example.com/{index}", "snippet", source)


class SearchFallbackTest(unittest.TestCase):
    def test_falls_back_after_network_failure(self):
        gateway = SearchGateway(
            [
                FakeProvider("first", error=SearchProviderError("timeout")),
                FakeProvider("second", [item("second", i) for i in range(3)]),
            ],
            min_results=3,
        )
        result = gateway.search("python", 5)
        self.assertTrue(result.degraded)
        self.assertEqual(["second"], result.provider_used)
        self.assertEqual(3, len(result.results))

    def test_auth_error_stops_fallback(self):
        gateway = SearchGateway(
            [
                FakeProvider("first", error=SearchProviderAuthError("invalid key")),
                FakeProvider("second", [item("second", 1)]),
            ]
        )
        result = gateway.search("python", 5)
        self.assertEqual([], result.results)
        self.assertEqual(1, len(result.attempts))

    def test_tool_rejects_local_absolute_path(self):
        gateway = SearchGateway([FakeProvider("search", [item("search", 1)])])
        result = WebSearchTool(gateway).invoke("查找 /Users/example/private.txt 的内容")
        self.assertFalse(result.ok)
        self.assertIn("本地绝对路径", result.error)

    def test_records_each_search_provider_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = TraceRecorder(Path(tmp) / "trace.jsonl")
            recorder.start_trace(session_id="session-1", mode="lcel")
            gateway = SearchGateway(
                [
                    FakeProvider("first", error=SearchProviderError("timeout")),
                    FakeProvider("second", [item("second", i) for i in range(3)]),
                ],
                min_results=3,
                recorder=recorder,
            )
            gateway.search("python", 5)
            entries = [
                json.loads(line)
                for line in recorder.path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                ["search_provider.failed", "search_provider.completed"],
                [entry["event"] for entry in entries],
            )
            self.assertTrue(all(entry["duration_ms"] is not None for entry in entries))


if __name__ == "__main__":
    unittest.main()
