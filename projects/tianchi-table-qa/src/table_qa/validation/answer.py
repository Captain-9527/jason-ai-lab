"""答案协议与格式规范；不从一段解释中猜测最终数字。"""
import json
import re
from decimal import Decimal, InvalidOperation

from ..schemas import PipelineError
from .table import validate_table

ANSWER_CONTRACT_VERSION = 23


def normalize_array_nulls(answer, policy=None):
    """仅按显式数组约定转换占位；不改顶层null、嵌套对象或原输入。"""
    if not isinstance(answer, list) or (policy or {}).get('array_nulls', 'reject') != 'empty_string':
        return answer
    return ['' if v is None or isinstance(v, str) and v.strip().casefold() == 'null' else v for v in answer]


def number(value):
    if isinstance(value, bool) or value is None:
        raise PipelineError("缺少有效数字")
    text = str(value).strip().replace("，", ",")
    if "," in text and not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", text):
        raise PipelineError("数字千分位格式不合法")
    text = text.replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
        raise PipelineError("数字包含不允许的单位、符号或解释")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise PipelineError("无效数字") from exc
    if not value.is_finite() or abs(value.adjusted()) > 100:
        raise PipelineError("数字非有限值或数量级超过上限")
    return value


def decimal_text(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def remove_grouping(value):
    """只处理整个值确为千分位数字的文字，不改变编号、标签或混合文本。"""
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?", value.strip()):
        return value.strip().replace(",", "").replace("，", "")
    return value


def answer_contract(q):
    """结构协议由题型决定；计数题不能因 JSON 标记而被误当作表格。"""
    if q.question_type == "structure":
        return "table"
    if q.answer_format == "json" and re.search(r"计数|数量|总数|个数|多少|几个|几项|几条", q.question):
        return "number"
    return q.answer_format or "string"


def width_text(value):
    return "".join(chr(ord(c) - 65248) if 65281 <= ord(c) <= 65374 else " " if c == "\u3000" else c for c in str(value))


def is_literal_marker(q, answer):
    """仅提取语境接受明确的非数值原文；不把缺测标记当作计算结果或零。"""
    return (answer_contract(q) == "number" and q.question_type == "extract"
            and q.route not in {"calculation", "filter"}
            and isinstance(answer, str)
            and answer.strip().casefold() in {"--", "-", "—", "–", "n/a", "na"})


def text_value(value, policy):
    """文本默认保留全半角；比较或历史兼容需要时可显式开启转换。"""
    return width_text(value) if policy.get("normalize_text_width", False) else str(value)


def numeric_text(answer, policy):
    text = width_text(answer).strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    percent = text.endswith("%")
    value = number(text[:-1].strip() if percent else text)
    if negative and value < 0:
        raise PipelineError("括号负数不能重复带负号")
    if percent and policy.get("percent", "preserve") == "fraction":
        value /= 100
    result = format(value, "f") if policy.get("preserve_decimal_zeros", True) else decimal_text(value)
    if percent and policy.get("percent", "preserve") == "preserve":
        result += "%"
    if negative:
        result = f"({result})" if policy.get("parentheses_negative", "preserve") == "preserve" else "-" + result
    return result


def format_answer(q, answer, policy=None):
    if answer is None:
        raise PipelineError("答案不可确定")
    policy = policy or {}
    kind = answer_contract(q)
    if kind == "table":
        if not isinstance(answer, dict):
            raise PipelineError("结构答案应为对象")
        missing = {'row_count', 'col_count', 'cells'} - answer.keys()
        if missing:
            raise PipelineError('answer须直接包含row_count、col_count、cells；禁止answer.table或其他包装')
        if isinstance(answer.get('cells'), list):
            for index, cell in enumerate(answer['cells']):
                if not isinstance(cell, dict) or not {'text', 'row', 'col', 'rowspan', 'colspan'} <= cell.keys():
                    raise PipelineError(f'cells[{index}]缺少text/row/col/rowspan/colspan；像素bbox不能替代0起逻辑网格')
        validate_table(answer)
        cells = []
        for cell in answer["cells"]:
            if cell["text"] is None:
                raise PipelineError("结构答案仍有无法辨认的单元格")
            cells.append({k: cell[k] for k in ("text", "row", "col", "rowspan", "colspan")})
        result = {"row_count": answer["row_count"], "col_count": answer["col_count"], "cells": cells}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if kind == "json":
        try:
            return json.dumps(answer, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise PipelineError("JSON 答案不合法") from exc
    if kind == "json_array":
        answer = normalize_array_nulls(answer, policy)
        if not isinstance(answer, list) or any(isinstance(v, (dict, list, bool)) or v is None for v in answer):
            raise PipelineError("数组必须由具体值组成；不确定值不能静默当作空白")
        try:
            values = [remove_grouping(text_value(v, policy)) if isinstance(v, str) else v for v in answer]
            style = policy.get('array_numbers', 'preserve')
            if style == 'string':
                values = [str(v) for v in values]
            if style == 'number':
                # 直接写Decimal的十进制文本，避免经float损失大金额精度；混合文本仍为字符串。
                items = []
                for value in values:
                    if isinstance(value, str) and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', value.strip()):
                        items.append(format(number(value), 'f'))
                    else:
                        items.append(json.dumps(value, ensure_ascii=False, allow_nan=False))
                return '[' + ','.join(items) + ']'
            return json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise PipelineError("数组答案不合法") from exc
    if kind == "number":
        if is_literal_marker(q, answer):
            return answer.strip()
        if isinstance(answer, bool):
            raise PipelineError("布尔值不是数字答案")
        return numeric_text(answer, policy)
    if kind != "string" or isinstance(answer, (dict, list, bool)):
        raise PipelineError("文本答案类型不正确")
    text = " ".join(str(remove_grouping(text_value(answer, policy))).split())
    if policy.get('language_names') == 'zh' and re.search(r'语言', q.question):
        aliases = {'english': '英文', '英语': '英文', 'chinese': '中文', '中英双语': '中文和英文',
                   '中英文混合': '中文和英文', '中英文': '中文和英文', 'chinese and english': '中文和英文'}
        text = aliases.get(text.casefold(), text)
    return text


def validate_candidate_answer(q, answer, policy=None):
    """新增候选不能以空字符串冒充完成；不改变历史导出和空白表格单元格。"""
    if isinstance(answer, str) and not width_text(answer).strip():
        raise PipelineError("答案为空字符串，不能判定为已完成")
    text = format_answer(q, answer, policy)
    if not text.strip():
        raise PipelineError("格式化后的答案为空")
    return text


def is_completed(q, record, policy=None):
    """状态和可提交内容同时有效才跳过；只检查，不改写历史记录。"""
    if record.get("status") != "success":
        return False
    try:
        validate_candidate_answer(q, record.get("answer"), policy)
    except (PipelineError, TypeError, KeyError, ValueError):
        return False
    return True


def canonical(q, answer):
    if is_literal_marker(q, answer):
        # 不把不同原文标记互相等同，更不能与数值 0 等同。
        return "literal:" + answer.strip()
    if answer_contract(q) == "number":
        return decimal_text(number(numeric_text(answer, {"percent": "fraction", "parentheses_negative": "convert"})))
    text = format_answer(q, answer)
    if q.question_type == "structure":
        obj = json.loads(text)
        obj["cells"].sort(key=lambda c: (c["row"], c["col"]))
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    if answer_contract(q) == "json":
        return json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
    return text


def comparison_details(q, left, right):
    """结构按维度报告差异；文本差异仍是冲突，不按结构一致悄悄放行。"""
    if q.question_type != "structure":
        return {"answer_equal": canonical(q, left) == canonical(q, right)}
    a, b = json.loads(format_answer(q, left)), json.loads(format_answer(q, right))
    def positions(table):
        return {(c["row"], c["col"], c["rowspan"], c["colspan"]): c["text"] for c in table["cells"]}
    ac, bc = positions(a), positions(b)
    return {"dimensions_equal": (a["row_count"], a["col_count"]) == (b["row_count"], b["col_count"]),
            "positions_equal": ac.keys() == bc.keys(), "text_equal": ac == bc}
