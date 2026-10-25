"""结构校验保持 null 与空白的区别，不静默把非法跨度改成 1。"""
import math
from copy import deepcopy

from ..schemas import PipelineError


def decode_recognized_table(raw):
    """识别传输数组还原为内部对象；兼容旧响应，不污染缓存原始对象。"""
    table = deepcopy(raw)
    if isinstance(table, dict) and isinstance(table.get("cells"), list):
        cells = []
        for cell in table["cells"]:
            if isinstance(cell, list):
                if len(cell) != 5:
                    raise PipelineError("紧凑单元格必须为 [text,row,col,rowspan,colspan]")
                cell = dict(zip(("text", "row", "col", "rowspan", "colspan"), cell))
            cells.append(cell)
        table["cells"] = cells
    return validate_table(table)


def validate_table(table):
    if not isinstance(table, dict):
        raise PipelineError("表格必须是对象")
    rows, cols, cells = table.get("row_count"), table.get("col_count"), table.get("cells")
    if type(rows) is not int or type(cols) is not int or rows <= 0 or cols <= 0 or rows * cols > 200000:
        raise PipelineError("表格尺寸非法或超过安全上限")
    if not isinstance(cells, list) or not cells or len(cells) > rows * cols:
        raise PipelineError("单元格列表为空或非法")
    if not isinstance(table.get("notes", []), list) or any(not isinstance(v, str) for v in table.get("notes", [])):
        raise PipelineError("表格 notes 必须为文字数组")
    occupied = set()
    for cell in cells:
        if not isinstance(cell, dict) or "text" not in cell or (cell["text"] is not None and not isinstance(cell["text"], str)):
            raise PipelineError("单元格 text 必须为字符串或 null")
        keys = ("row", "col", "rowspan", "colspan")
        if any(type(cell.get(k)) is not int for k in keys):
            raise PipelineError("单元格坐标与跨度必须为整数")
        r, c, rs, cs = [cell[k] for k in keys]
        if r < 0 or c < 0 or rs < 1 or cs < 1 or r + rs > rows or c + cs > cols:
            raise PipelineError(f"单元格越界：row={r},col={c},rowspan={rs},colspan={cs}，整表={rows}×{cols}；坐标0起，不自动截断")
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                if (rr, cc) in occupied:
                    raise PipelineError(f"单元格覆盖区域重叠：row={r},col={c},rowspan={rs},colspan={cs}重复覆盖({rr},{cc})；合并格只输出一次")
                occupied.add((rr, cc))
    return table


def bbox(value, width, height):
    if not isinstance(value, list) or len(value) != 4:
        raise PipelineError("bbox 必须有四个像素坐标")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise PipelineError("bbox 坐标非法")
    left, top, right, bottom = [round(v) for v in value]
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise PipelineError("bbox 越界或为空")
    return [left, top, right, bottom]
