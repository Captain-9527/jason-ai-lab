"""仅用于导出的非空答案选择；不改变原始候选、状态或证据。"""
from .answer import answer_contract, validate_candidate_answer


def nonempty_answer(q, record, policy=None, baseline=None):
    """优先保留最终选择；其次复用可校验候选，最后明确标识占位值。"""
    record = record or {}
    choices = [("result", record), ("selected_candidate", record.get("selected_candidate") or {})]
    candidates = record.get("candidates") or {}
    preferred = (record.get("review") or {}).get("preferred")
    def rank(item):
        name, value = item
        return (name == preferred, bool(value.get("computation_checked")),
                bool(value.get("repair_after_retry") and value.get("review_verdict") != "retry"),
                bool(value.get("evidence")), name == "direct")
    choices.extend((f"candidate:{name}", value) for name, value in
                   sorted(candidates.items(), key=rank, reverse=True))
    choices.extend([("baseline_candidate", record.get("baseline_candidate") or {}),
                    ("baseline", baseline or {})])
    rejected = []
    for source, candidate in choices:
        if candidate.get("answer") is None:
            continue
        try:
            text = validate_candidate_answer(q, candidate["answer"], policy)
            if len(text) > 32767:
                raise ValueError("超过 Excel 单元格字符上限")
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"source": source, "reason": str(exc)})
            continue
        return text, {"source": source, "placeholder": False, "rejected": rejected,
                      "raw_answer": candidate["answer"],
                      "source_run_id": candidate.get("source_run_id") or candidate.get("inherited_from")}
    kind = answer_contract(q)
    defaults = {"number": 0, "string": "无法确定", "json": {}, "json_array": ["无法确定"],
                "table": {"row_count": 1, "col_count": 1, "cells": [
                    {"text": "无法确定", "row": 0, "col": 0, "rowspan": 1, "colspan": 1}]}}
    # 占位仅满足本地类型契约，不意味着识别成功或答案正确。
    text = validate_candidate_answer(q, defaults[kind], policy)
    return text, {"source": "placeholder", "placeholder": True, "rejected": rejected,
                  "raw_answer": None,
                  "reason": "没有格式合法的非空候选；未调用模型补答"}
