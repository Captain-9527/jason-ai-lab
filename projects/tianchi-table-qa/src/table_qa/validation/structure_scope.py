"""区分整表尺寸与局部表头cells；不从模型计数推断真实答案。"""
import re

from ..schemas import PipelineError


def structure_scope(q):
    if re.search(r'表头|\bheaders?\b', q.question, re.I):
        return 'header_only'
    return 'requested_cells'


def requested_cell_ranges(q):
    """只解析明确的前N行与前M列联合范围；复杂选区不猜。"""
    digits = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    def count(axis):
        match = re.search(r'(?:前\s*([1-9]\d*|[一二两三四五六七八九十])|第\s*(1|一))\s*' + axis, q.question)
        if not match:
            return None
        value = match[1] or match[2]
        return int(value) if value.isdigit() else digits[value]
    rows, cols = count('行'), count('列')
    if rows is None or cols is None or re.search(r'不含|不包括|排除|仅交叉|交点|相交', q.question):
        return None
    return {'leading_rows': rows, 'leading_columns': cols, 'operator': 'union'}


def has_full_structure_check(q, record):
    """仅检查存量候选有无当前整表协议依据，不把旧success视为尺寸证明。"""
    candidate = record.get('selected_candidate') or record
    answer = record.get('answer')
    check = candidate.get('full_table_check')
    if not isinstance(answer, dict) or not isinstance(check, dict):
        return False
    counts = check.get('row_breakdown')
    if (check.get('scope') != 'full_table' or not isinstance(counts, dict)
            or type(check.get('page')) is not int or check['page'] < 1
            or not isinstance(check.get('basis'), str) or not check['basis'].strip()
            or any(type(check.get(k)) is not int or check[k] != answer.get(k)
                   for k in ('row_count', 'col_count'))
            or any(type(counts.get(k)) is not int or counts[k] < 0 for k in ('header','body','notes'))
            or sum(counts[k] for k in ('header','body','notes')) != answer['row_count']):
        return False
    try:
        validate_structure_scope(q, answer, check)
    except (ValueError, TypeError, KeyError):
        return False
    return True


def validate_structure_scope(q, answer, check):
    requested = requested_cell_ranges(q)
    if requested:
        rows, cols = min(requested['leading_rows'], answer['row_count']), min(requested['leading_columns'], answer['col_count'])
        covered = set()
        for cell in answer['cells']:
            coordinates = {(r, c) for r in range(cell['row'], cell['row'] + cell['rowspan'])
                           for c in range(cell['col'], cell['col'] + cell['colspan'])}
            if not any(r < rows or c < cols for r, c in coordinates):
                raise PipelineError('局部结构含未请求的单元格：应输出前N行与前M列的联合范围，合并格只保留一次')
            covered.update(coordinates)
        expected = {(r, c) for r in range(answer['row_count']) for c in range(answer['col_count'])
                    if r < rows or c < cols}
        if not expected <= covered:
            raise PipelineError('局部结构遗漏前N行或前M列：第一列须覆盖数据区，不是只输出表头；不自动补造文字')
    if structure_scope(q) != 'header_only':
        return
    counts = check['row_breakdown']
    header = counts['header']
    if header < 1:
        raise PipelineError('题目仅恢复表头，但未声明合法表头行数')
    if counts['body'] == 0 and check.get('empty_body') is not True:
        raise PipelineError('局部表头题仍须统计整表数据行；body=0必须明确empty_body=true，不能只数表头')
    ranges = check.get('header_ranges', [[0, header]])
    if (not isinstance(ranges, list) or not ranges
            or any(not isinstance(v, list) or len(v) != 2 or any(type(x) is not int for x in v)
                   or not 0 <= v[0] < v[1] <= answer['row_count'] for v in ranges)
            or sum(v[1]-v[0] for v in ranges) != header
            or any(a[1] > b[0] for a, b in zip(sorted(ranges), sorted(ranges)[1:]))):
        raise PipelineError('header_ranges须为互不重叠的0起半开表头行区间，长度之和等于header计数')
    for cell in answer['cells']:
        if not any(start <= cell['row'] and cell['row']+cell['rowspan'] <= end for start, end in ranges):
            raise PipelineError(f"题目仅要求表头cells，row={cell['row']}、rowspan={cell['rowspan']}超出header={header}；保留整表尺寸但不要输出数据行")
    regions = check.get('header_regions')
    expected = None
    if regions is not None:
        if (not isinstance(regions, list) or not regions
                or any(not isinstance(v, list) or len(v) != 4 or any(type(x) is not int for x in v)
                       or not 0 <= v[0] < v[1] <= answer['row_count']
                       or not 0 <= v[2] < v[3] <= answer['col_count']
                       or not any(start <= v[0] < v[1] <= end for start, end in ranges) for v in regions)):
            raise PipelineError('header_regions须为表头行区间内合法的0起半开矩形')
        expected = set()
        for r0, r1, c0, c1 in regions:
            area = {(r, c) for r in range(r0, r1) for c in range(c0, c1)}
            if expected & area:
                raise PipelineError('header_regions不能重叠')
            expected.update(area)
        if {r for r, _ in expected} != {r for start, end in ranges for r in range(start, end)}:
            raise PipelineError('header_regions须覆盖每个声明的表头行')
        for cell in answer['cells']:
            area = {(r, c) for r in range(cell['row'], cell['row'] + cell['rowspan'])
                    for c in range(cell['col'], cell['col'] + cell['colspan'])}
            if not area <= expected:
                raise PipelineError('嵌套表头cells超出声明的真实表头区域，不能包含旁侧数据格')
    groups = check.get('nested_groups')
    if groups is not None:
        if not isinstance(groups, list) or not groups or expected is None:
            raise PipelineError('嵌套分组须为非空数组，并同时声明header_regions')
        header_rows = {r for start, end in ranges for r in range(start, end)}
        body_rows = set(range(answer['row_count'] - counts['notes'])) - header_rows
        covered_body = set()
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('label'), str) or not group['label'].strip():
                raise PipelineError('嵌套分组须保留外层原名')
            spans = [group.get(k) for k in ('header_range', 'body_range', 'columns')]
            if any(not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v) for v in spans):
                raise PipelineError('嵌套分组须声明表头/数据行范围及列范围')
            (h0,h1),(b0,b1),(c0,c1) = spans
            if not (0 <= h0 < h1 <= b0 < b1 <= answer['row_count'] - counts['notes']
                    and 0 <= c0 < c1 <= answer['col_count']):
                raise PipelineError('嵌套分组范围越界或顺序矛盾')
            head = {(r,c) for r in range(h0,h1) for c in range(c0,c1)}
            body = set(range(b0,b1))
            if not head <= expected or not body <= body_rows or body & covered_body:
                raise PipelineError('嵌套分组表头未声明、数据与表头冲突或外层组重复')
            covered_body |= body
        if covered_body != body_rows:
            raise PipelineError('嵌套分组未完整覆盖全部数据行，可能漏外层组及其重复表头')
    if re.search(r'表头行结构|完整表头|全部表头|\ball headers\b', q.question, re.I):
        covered = sum(c['rowspan']*c['colspan'] for c in answer['cells'])
        if covered != (len(expected) if expected is not None else header*answer['col_count']):
            raise PipelineError('完整表头未覆盖声明的全部列/层级；空白角格和分组列仍须输出，合并格用跨度覆盖')
