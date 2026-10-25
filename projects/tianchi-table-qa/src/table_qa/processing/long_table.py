"""模型判断边界，程序执行列映射、去重和拼接；不支持的跨度宁可报错。"""
from copy import deepcopy
import re

from ..schemas import PipelineError
from ..validation.table import validate_table


def _header_text(text):
    if text is None:
        return None
    # 仅比较时规范全角ASCII、空白和括号边界，不改原文，不删除词间空格。
    text = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)
    text = " ".join(text.split())
    text = re.sub(r"\s*([()])\s*", r"\1", text)
    return re.sub(r"(?<=注)\s+(?=\d)", "", text)


def _row_signature(table, row, header=False):
    return sorted((c["col"], c["colspan"], c["rowspan"], _header_text(c["text"]) if header else c["text"])
                  for c in table["cells"] if c["row"] == row)


def merge_tables(left, right, relation):
    validate_table(left)
    validate_table(right)
    if relation.get("same_table") is not True:
        return None
    if relation.get("warnings"):
        raise PipelineError("跨段关系存在未解决风险，拒绝自动合并")
    r = deepcopy(right)
    n = left["col_count"]
    order = relation.get("column_order")
    if right["col_count"] != n or not isinstance(order, list) or any(type(v) is not int for v in order) or sorted(order) != list(range(n)):
        raise PipelineError("跨段列映射不是一一对应")
    for c in r["cells"]:
        mapped = [order[i] for i in range(c["col"], c["col"] + c["colspan"])]
        if mapped != list(range(min(mapped), max(mapped) + 1)):
            raise PipelineError("合并单元格无法在列重排后保持连续")
        c["col"] = min(mapped)
    header, overlap = relation.get("drop_header_rows", 0), relation.get("overlap_rows", 0)
    if any(type(v) is not int or v < 0 for v in (header, overlap)):
        raise PipelineError("重复行数非法")
    drop = header + overlap
    if drop >= r["row_count"] or header > left["row_count"] or overlap > left["row_count"]:
        raise PipelineError("删除重复行会丢失整个片段")
    for i in range(header):
        if _row_signature(left, i, header=True) != _row_signature(r, i, header=True):
            raise PipelineError("重复表头内容不一致，需复核而非直接删除")
    for i in range(overlap):
        if _row_signature(left, left["row_count"] - overlap + i) != _row_signature(r, header + i):
            raise PipelineError("重叠行内容不一致")
    for c in r["cells"]:
        if c["row"] < drop < c["row"] + c["rowspan"]:
            raise PipelineError("去重边界穿过合并单元格")
    r["cells"] = [c for c in r["cells"] if c["row"] >= drop]
    result = deepcopy(left)
    continued = relation.get("continued_row", False)
    if type(continued) is not bool:
        raise PipelineError("continued_row 必须是布尔值")
    offset = left["row_count"] - drop - int(continued)
    if continued:
        tail = [c for c in result["cells"] if c["row"] + c["rowspan"] == left["row_count"]]
        head = [c for c in r["cells"] if c["row"] == drop]
        if any(c["rowspan"] != 1 for c in tail + head) or {(c["col"], c["colspan"]) for c in tail} != {(c["col"], c["colspan"]) for c in head}:
            raise PipelineError("复杂跨页 rowspan 无法安全续接，需重新识别")
        mapping = {(c["col"], c["colspan"]): c for c in tail}
        for c in head:
            previous = mapping[c["col"], c["colspan"]]
            a, b = previous["text"], c["text"]
            if a is None or b is None:
                previous["text"] = None
            elif a != b:
                previous["text"] = "\n".join(v for v in (a, b) if v)
            previous.setdefault("sources", []).extend(c.get("sources", []))
        r["cells"] = [c for c in r["cells"] if c["row"] != drop]
    for c in r["cells"]:
        c["row"] += offset
    result["cells"].extend(r["cells"])
    result["row_count"] += right["row_count"] - drop - int(continued)
    result["notes"] = list(dict.fromkeys(left.get("notes", []) + right.get("notes", [])))
    result.setdefault("segments", []).extend(right.get("segments", []))
    return validate_table(result)
