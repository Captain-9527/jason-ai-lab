"""模型选择证据和运算，程序生成结构答案并执行计算。"""
from copy import deepcopy

from ..schemas import PipelineError
from ..validation.answer import answer_contract, validate_candidate_answer
from .calculator import execute_plan


def compact_tables(tables):
    """只压缩传输视图；本地完整表格仍用于引用、计算及坐标回查。"""
    return [{**{k: t[k] for k in ("table_id", "row_count", "col_count", "notes", "title", "unit",
                                  "headers", "footnotes", "incomplete", "integrity_warnings") if k in t},
             "pages": sorted({s['page'] for s in t.get('segments', [])} |
                             {s['page'] for c in t['cells'] for s in c.get('sources', [])}),
             "cells": [{k: c[k] for k in ("id", "text", "row", "col", "rowspan", "colspan") if k in c}
                       for c in t["cells"]]} for t in tables]


def solve_structured(q, tables, model, fresh=False):
    kind = "thinking" if q.route in {"calculation", "filter"} else q.question_type
    incomplete = any(t.get("incomplete") for t in tables)
    if incomplete and kind == "structure":
        raise PipelineError("结构化表格存在未恢复的片段，不能输出整表尺寸或结构")
    allowed = ["structure"] if kind == "structure" else ["extract", "calculate"]
    if incomplete:
        allowed = ["extract"]  # 不允许模型通过片段计数/汇总伪造完整性。
    question = {**q.public(), "solver_type": kind, "allowed_operations": allowed, "answer_contract": answer_contract(q)}
    raw = model.ask("structured", {"question": question, "tables": compact_tables(tables)}, vision=False, fresh=fresh)
    if raw.get("unanswerable"):
        raise PipelineError("结构化证据不足以答题")
    # 旧响应按原协议解读；新响应必须明确声明操作，题型不再限制非结构题只能提取。
    operation = raw.get("operation", "structure" if kind == "structure" else "extract" if kind == "extract" else "calculate")
    if operation not in allowed:
        raise PipelineError("structured operation 不合法或与结构题冲突")
    fields = {"structure": {"table_id", "cell_ids", "parts", "layout"}, "extract": {"values"}, "calculate": {"plan", "plans"}}
    if any(key in raw for name, keys in fields.items() if name != operation for key in keys):
        raise PipelineError("structured 响应混合了不同操作协议")
    cells = {c["id"]: c for t in tables for c in t["cells"]}
    refs = []
    if operation == "structure":
        parts = raw.get("parts")
        if parts is not None:
            if (raw.get("layout") != "vertical" or "table_id" in raw or "cell_ids" in raw
                    or not isinstance(parts, list) or not 2 <= len(parts) <= 20):
                raise PipelineError("多表结构须显式指定vertical布局与至少两个parts，不能混用单表协议")
        else:
            if raw.get("layout") not in {None, "single"}:
                raise PipelineError("单表结构不接受其他布局")
            parts = [{"table_id": raw.get("table_id"), "cell_ids": raw.get("cell_ids")}]
        answer = {"row_count": 0, "col_count": 0, "cells": []}
        selected_tables = set()
        for part in parts:
            if not isinstance(part, dict):
                raise PipelineError("结构片段必须为对象")
            table = next((t for t in tables if t["table_id"] == part.get("table_id")), None)
            chosen = part.get("cell_ids")
            if (table is None or table["table_id"] in selected_tables or not isinstance(chosen, list) or not chosen
                    or any(not isinstance(ref, str) for ref in chosen) or len(chosen) != len(set(chosen))):
                raise PipelineError("结构题未选择有效表格和单元格，或重复选择同表")
            selected_tables.add(table["table_id"])
            allowed = {c["id"] for c in table["cells"]}
            if not set(chosen).issubset(allowed):
                raise PipelineError("局部结构包含其他表格的单元格")
            for cell in table["cells"]:
                if cell["id"] in chosen:
                    item = deepcopy(cell)
                    item["row"] += answer["row_count"]
                    answer["cells"].append(item)
            refs.extend(chosen)
            answer["row_count"] += table["row_count"]
            answer["col_count"] = max(answer["col_count"], table["col_count"])
    elif operation == "extract":
        values = raw.get("values")
        if not isinstance(values, list) or not values:
            raise PipelineError("提取结果为空")
        answer_values = []
        for v in values:
            if not isinstance(v, dict) or v.get("ref") not in cells:
                raise PipelineError("提取结果没有有效单元格引用")
            text = v.get("text")
            original = cells[v["ref"]]["text"]
            if original is None or not isinstance(text, str) or text not in original or (text == "" and original != ""):
                raise PipelineError("提取结果不是引用单元格中的原文")
            answer_values.append(text)
            refs.append(v["ref"])
        if q.answer_format == "json_array":
            answer = answer_values
        elif len(answer_values) == 1:
            answer = answer_values[0]
        else:
            raise PipelineError("标量答案意外提取了多个值")
    else:
        if "plan" in raw and "plans" in raw:
            raise PipelineError("plan 与 plans 不能同时提供")
        plans = raw.get("plans") or ([raw["plan"]] if "plan" in raw else [])
        if not plans or not isinstance(plans, list) or len(plans) > 100:
            raise PipelineError("缺少合法计算计划")
        values = []
        for plan in plans:
            result, used = execute_plan(plan, cells, tables)
            values.append(result)
            refs.extend(used)
        answer = values[0] if len(values) == 1 else values
        if q.answer_format == "json_array" and not isinstance(answer, list):
            answer = [answer]
    validate_candidate_answer(q, answer)
    evidence = []
    for ref in dict.fromkeys(refs):
        for source in cells[ref].get("sources", []):
            item = {"cell_id": ref, "page": source["page"], "bbox": source["bbox"]}
            if item not in evidence:
                evidence.append(item)
    if not evidence and operation == "calculate" and all(p.get("op") == "count" for p in plans):
        # 空集合计数仍需把待查范围作为证据，而不是把 0 当作无答案。
        for table in tables:
            evidence.extend({"page": s["page"], "bbox": s["bbox"]} for s in table.get("segments", []))
    if not evidence:
        raise PipelineError("答案缺少可回溯证据")
    source_cells = [{k: cells[ref][k] for k in ("id", "text", "row", "col", "rowspan", "colspan") if k in cells[ref]}
                    for ref in dict.fromkeys(refs)]
    return {"answer": answer, "evidence": evidence, "operation": operation,
            "grounding": raw.get('grounding'), "items": raw.get('items'),
            "scope_check": raw.get('scope_check'), "full_table_check": raw.get('full_table_check'),
            "facts": raw.get('facts'),
            "structure_layout": raw.get("layout", "single") if operation == "structure" else None,
            "structure_parts": raw.get("parts") if operation == "structure" else None,
            "plan": raw if operation == "calculate" else None, "source_cells": source_cells,
            "warnings": ["结构化识别不完整，仅采用片段原文提取，需原图复核"] if incomplete else [],
            "requires_review": incomplete,
            "computation_checked": operation == "calculate"}
