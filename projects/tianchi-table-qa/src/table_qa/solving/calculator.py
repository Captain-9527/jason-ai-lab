"""不运行模型代码；模型只提交声明式运算和已识别单元格引用。"""
import re
from math import prod
from decimal import Decimal, ROUND_HALF_UP, localcontext

from ..schemas import PipelineError
from ..validation.answer import number, decimal_text
from ..validation.numeric_locale import normalize_numeric_text, NUMERIC_FORMATS

OPS = {"sum", "product", "mean", "difference", "absolute_difference", "ratio", "growth_rate", "min", "max", "count", "sort", "select", "argmax", "argmin"}


def numeric(text, numeric_format='standard'):
    text = normalize_numeric_text(text, numeric_format)
    percent = text.endswith("%") or text.endswith("％")
    if percent:
        text = text[:-1]
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    result = number(text)
    return result / 100 if percent else result


def validate_predicate(predicate):
    if not isinstance(predicate, dict) or predicate.get("operator") not in {"gt", "ge", "lt", "le", "eq", "ne"}:
        raise PipelineError("筛选条件不合法")
    if predicate.get("missing", "error") not in {"error", "exclude"}:
        raise PipelineError("缺测筛选策略不合法")
    if predicate.get("range_policy", "all") not in {"all", "any", "lower", "upper"}:
        raise PipelineError("区间筛选策略不合法")
    return numeric(predicate.get("value"))


def matches_predicate(text, predicate, numeric_format='standard'):
    target = validate_predicate(predicate)
    if text is None or str(text).strip().casefold() in {"", "--", "-", "—", "–", "n/a", "na"}:
        if predicate.get("missing") == "exclude":
            return False
        raise PipelineError("筛选范围包含缺测值，不能静默排除")
    try:
        lower = upper = numeric(text, numeric_format)
    except PipelineError:
        value = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)[%％]?)"
        match = re.fullmatch(value + r"\s*(?:～|~|至|–|—|-)\s*" + value, str(text).strip())
        if match is None:
            raise PipelineError("筛选值不是数字或支持的闭区间") from None
        lower, upper = numeric(match[1]), numeric(match[2])
        if lower > upper:
            raise PipelineError("筛选区间上下限反向")
        if predicate.get("range_policy") not in {"all", "any", "lower", "upper"}:
            raise PipelineError("区间筛选必须明确 range_policy")
    policy = predicate.get("range_policy", "all")
    if policy == "lower":
        upper = lower
    elif policy == "upper":
        lower = upper
    op = predicate["operator"]
    if op in {"gt", "ge"}:
        bound = upper if policy == "any" else lower
        return bound > target if op == "gt" else bound >= target
    if op in {"lt", "le"}:
        bound = lower if policy == "any" else upper
        return bound < target if op == "lt" else bound <= target
    if op == "eq":
        return lower <= target <= upper if policy == "any" else lower == upper == target
    return (lower, upper) != (target, target) if policy == "any" else target < lower or target > upper


def list_result(plan, values):
    separator = plan.get("separator")
    if separator is None:
        return values
    if not isinstance(separator, str) or not 1 <= len(separator) <= 8:
        raise PipelineError("列表连接符必须为1到8个字符")
    return separator.join(str(v) for v in values)


def calculate(plan, values, labels=None):
    if not isinstance(plan, dict) or plan.get("op") not in OPS or not isinstance(values, list):
        raise PipelineError("不支持的运算计划")
    op = plan["op"]
    formats = plan.get('numeric_formats', ['standard'] * len(values))
    if not isinstance(formats, list) or len(formats) != len(values):
        raise PipelineError('numeric_formats须与操作数逐项等长')
    if any(not isinstance(style, str) or style not in NUMERIC_FORMATS for style in formats):
        raise PipelineError('numeric_formats包含未知地区格式')
    if labels is not None and (not isinstance(labels, list) or len(labels) != len(values) or any(v is None for v in labels)):
        raise PipelineError("标签与数值数量不一致或标签缺失")
    if "separator" in plan and op not in {"select", "sort", "argmax", "argmin"}:
        raise PipelineError("仅列表操作允许 separator")
    if "predicate" in plan:
        validate_predicate(plan["predicate"])
        indices = [i for i, value in enumerate(values) if matches_predicate(value, plan["predicate"], formats[i])]
        values = [values[i] for i in indices]
        formats = [formats[i] for i in indices]
        labels = [labels[i] for i in indices] if labels is not None else None
    if not values and op not in {"count", "select"}:
        raise PipelineError("计算操作数为空")
    if any(v is None for v in values):
        raise PipelineError("操作数含未识别内容")
    if op == "select":
        return list_result(plan, labels if labels is not None else values)
    if op == "count":
        return str(len(values))
    with localcontext() as ctx:
        ctx.prec = 64
        nums = [numeric(v, style) for v, style in zip(values, formats)]
        if op in {"difference", "absolute_difference", "ratio", "growth_rate"} and len(nums) != 2:
            raise PipelineError("差额/比例/增长率必须有两个操作数")
        if op in {"ratio", "growth_rate"} and nums[1] == 0:
            raise PipelineError("分母为零")
        if op in {"argmax", "argmin"}:
            if not isinstance(labels, list) or len(labels) != len(nums) or any(v is None for v in labels):
                raise PipelineError("极值标签与数值数量不一致")
            target = max(nums) if op == "argmax" else min(nums)
            chosen = [labels[i] for i, v in enumerate(nums) if v == target]
            return chosen[0] if len(chosen) == 1 and "separator" not in plan else list_result(plan, chosen)
        descending = plan.get("descending", False)
        if type(descending) is not bool:
            raise PipelineError("descending 必须为布尔值")
        if op == "sort":
            return list_result(plan, [decimal_text(v) for v in sorted(nums, reverse=descending)])
        result = {"sum": lambda: sum(nums), "product": lambda: prod(nums), "mean": lambda: sum(nums) / len(nums),
                  "difference": lambda: nums[0] - nums[1], "absolute_difference": lambda: abs(nums[0] - nums[1]), "ratio": lambda: nums[0] / nums[1],
                  "growth_rate": lambda: (nums[0] - nums[1]) / nums[1],
                  "min": lambda: min(nums), "max": lambda: max(nums)}[op]()
        result *= number(plan.get("scale", "1"))
        digits = plan.get("digits")
        if digits is not None:
            if type(digits) is not int or not 0 <= digits <= 12:
                raise PipelineError("digits 必须为 0 到 12 的整数")
            result = result.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        suffix = plan.get("suffix", "")
        if suffix not in {"", "%"}:
            raise PipelineError("不支持的数值后缀")
        return (format(result, f".{digits}f") if digits is not None else decimal_text(result)) + suffix


def execute_plan(plan, cells, tables=None):
    if not isinstance(plan, dict):
        raise PipelineError("计算计划必须为对象")
    refs = plan.get("refs")
    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in cells for r in refs):
        raise PipelineError("计算包含未知单元格引用")
    if plan.get("op") == "count" and len(refs) != len(set(refs)):
        raise PipelineError("计数不能重复引用同一单元格，合并单元格只计一次")
    domain = plan.get("domain")
    if domain is not None:
        if not isinstance(domain, dict):
            raise PipelineError("筛选范围必须为对象")
        table = next((t for t in (tables or []) if t.get("table_id") == domain.get("table_id")), None)
        start, end, column = (domain.get(k) for k in ("start_row", "end_row", "column"))
        if (table is None or any(type(v) is not int for v in (start, end, column))
                or not 0 <= start < end <= table["row_count"] or not 0 <= column < table["col_count"]):
            raise PipelineError("筛选范围越界或目标表不存在")
        expected = {c["id"] for c in table["cells"]
                    if c["row"] < end and c["row"] + c["rowspan"] > start
                    and c["col"] <= column < c["col"] + c["colspan"]}
        excluded = domain.get("exclude_refs", [])
        if (not isinstance(excluded, list) or any(not isinstance(r, str) or r not in expected for r in excluded)
                or len(refs) != len(set(refs)) or set(refs) != expected - set(excluded)):
            raise PipelineError("筛选引用未完整覆盖声明范围，或包含重复/无关单元格")
    labels = plan.get("labels")
    if labels is not None and (not isinstance(labels, list) or any(not isinstance(r, str) or r not in cells for r in labels)):
        raise PipelineError("计算标签引用不存在")
    if domain and labels is not None:
        allowed_labels = {c["id"] for c in table["cells"]}
        if len(labels) != len(refs) or any(label not in allowed_labels or
                cells[label]["row"] >= cells[ref]["row"] + cells[ref]["rowspan"] or
                cells[ref]["row"] >= cells[label]["row"] + cells[label]["rowspan"]
                for ref, label in zip(refs, labels)):
            raise PipelineError("筛选标签必须来自目标表的对应数据行")
    result = calculate(plan, [cells[r]["text"] for r in refs],
                       [cells[r]["text"] for r in labels] if labels is not None else None)
    return result, refs + (labels or []) + (domain.get("exclude_refs", []) if domain else [])
