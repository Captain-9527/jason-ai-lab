"""搜索源顺序降级与聚合策略。"""

from __future__ import annotations

import time

from core.exceptions import SearchProviderAuthError, SearchProviderError
from core.tracing import TraceRecorder
from search.providers.base import SearchProvider
from search.schemas import SearchAttempt, SearchItem, SearchResponse


class SearchGateway:
    def __init__(
        self,
        providers: list[SearchProvider],
        *,
        strategy: str = "sequential",
        min_results: int = 3,
        recorder: TraceRecorder | None = None,
    ):
        if strategy not in {"sequential", "aggregate"}:
            raise ValueError("搜索策略仅支持 sequential 或 aggregate。")
        self.providers = providers
        self.strategy = strategy
        self.min_results = min_results
        self.recorder = recorder

    @staticmethod
    def _deduplicate(items: list[SearchItem], limit: int) -> list[SearchItem]:
        seen: set[str] = set()
        result: list[SearchItem] = []
        for item in items:
            if not item.url or item.url in seen:
                continue
            seen.add(item.url)
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def search(self, query: str, max_results: int) -> SearchResponse:
        if not query.strip():
            raise ValueError("搜索关键词不能为空。")
        response = SearchResponse(query=query, strategy=self.strategy)
        collected: list[SearchItem] = []
        eligible_index = 0
        for provider in self.providers:
            if not provider.available:
                response.attempts.append(
                    SearchAttempt(provider.name, "unavailable", "未启用或缺少密钥")
                )
                if self.recorder:
                    self.recorder.record(
                        event="search_provider.skipped",
                        component=provider.name,
                        status="unavailable",
                        input_data={"query": query, "max_results": max_results},
                        output_data={"reason": "未启用或缺少密钥"},
                    )
                continue
            eligible_index += 1
            started = time.perf_counter()
            try:
                results = provider.search(query, max_results)
            except SearchProviderAuthError as exc:
                duration_ms = (time.perf_counter() - started) * 1000
                response.attempts.append(
                    SearchAttempt(provider.name, "auth_error", str(exc), duration_ms=duration_ms)
                )
                if self.recorder:
                    self.recorder.record(
                        event="search_provider.failed",
                        component=provider.name,
                        status="auth_error",
                        input_data={"query": query, "max_results": max_results},
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                break
            except SearchProviderError as exc:
                duration_ms = (time.perf_counter() - started) * 1000
                response.attempts.append(
                    SearchAttempt(provider.name, "failed", str(exc), duration_ms=duration_ms)
                )
                if self.recorder:
                    self.recorder.record(
                        event="search_provider.failed",
                        component=provider.name,
                        status="error",
                        input_data={"query": query, "max_results": max_results},
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                response.degraded = True
                continue

            duration_ms = (time.perf_counter() - started) * 1000
            response.attempts.append(
                SearchAttempt(
                    provider.name,
                    "success",
                    result_count=len(results),
                    duration_ms=duration_ms,
                )
            )
            if self.recorder:
                self.recorder.record(
                    event="search_provider.completed",
                    component=provider.name,
                    status="success",
                    input_data={"query": query, "max_results": max_results},
                    output_data={"result_count": len(results), "results": results},
                    duration_ms=duration_ms,
                )
            if results:
                response.provider_used.append(provider.name)
                collected.extend(results)
            if self.strategy == "sequential" and len(results) >= self.min_results:
                response.degraded = response.degraded or eligible_index > 1
                response.results = self._deduplicate(results, max_results)
                return response
            if self.strategy == "sequential" and len(results) < self.min_results:
                response.degraded = True

        response.results = self._deduplicate(collected, max_results)
        return response
