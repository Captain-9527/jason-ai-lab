"""阶段统计兼容旧日志，以 request_id 关联响应；不把缓存或未知用量当作付费调用。"""
from collections import Counter


def efficiency_stats(events, results):
    requests, stages = {}, {}
    for event in events:
        name = event["event"]
        if name == "model_attempt" and event.get("request_id"):
            requests[event["request_id"]] = event.get("stage", "unknown")
        stage = event.get("stage") or requests.get(event.get("request_id"))
        if not stage or name not in {"model_attempt", "model_response", "model_error", "cache_hit"}:
            continue
        row = stages.setdefault(stage, Counter())
        if name == "model_attempt":
            row["attempts"] += 1
        elif name == "cache_hit":
            row["local_cache_hits"] += 1
        elif name == "model_error":
            row["errors"] += 1
            row["error_seconds"] += event.get("duration_seconds", 0)
            if event.get("error_type") == "APITimeoutError":
                row["timeouts"] += 1
        else:
            row["responses"] += 1
            row["response_seconds"] += event.get("duration_seconds", 0)
            usage = event.get("usage") or {}
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                row[field] += usage.get(field, 0) or 0
            reason = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if reason is not None:
                row["reasoning_reported_responses"] += 1
                row["reasoning_tokens"] += reason
                if usage.get("completion_tokens") is not None:
                    row["answer_tokens_estimate"] += max(0, usage["completion_tokens"] - reason)
            if cached is not None:
                row["cached_reported_responses"] += 1
                row["server_cached_tokens"] += cached
    output = {}
    for name, row in stages.items():
        output[name] = {k: row[k] for k in ("attempts", "responses", "errors", "timeouts", "local_cache_hits",
                                          "prompt_tokens", "completion_tokens", "total_tokens",
                                          "response_seconds", "error_seconds", "reasoning_reported_responses",
                                          "cached_reported_responses")}
        output[name].update(
            reasoning_tokens=row["reasoning_tokens"] if row["reasoning_reported_responses"] else None,
            answer_tokens_estimate=row["answer_tokens_estimate"] if row["reasoning_reported_responses"] else None,
            server_cached_tokens=row["server_cached_tokens"] if row["cached_reported_responses"] else None)
    local = [r for r in results.values() if not r.get("inherited_from")]
    attempted = [r for r in local if r.get("routing", {}).get("strategy") in {"structured", "compare"}]
    available = sum("structured" in r.get("candidates", {}) for r in attempted)
    return {"stage_usage": output,
            "table_merge_fallback_count": sum(e["event"] == "table_merge_fallback" for e in events),
            "table_recognition_failure_count": sum(e["event"] == "table_recognition_failed" for e in events),
            "candidate_quality": {"local_question_count": len(local),
                                  "structured_question_count": len(attempted),
                                  "structured_available_count": available,
                                  "structured_available_rate": available / len(attempted) if attempted else None,
                                  "reviewed_question_count": sum(any(v and not v.get("program_check")
                                                                       for v in [r.get("review", {}), *r.get("review_history", [])]) for r in local),
                                  "repaired_question_count": sum(any(k.startswith("repair_") for k in r.get("candidates", {})) for r in local)}}
