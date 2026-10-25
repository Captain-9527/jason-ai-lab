"""只读PDF矢量单元格几何：不使用可能损坏的文字编码生成答案文本。"""
import threading

from ..schemas import PipelineError
from ..validation.table import validate_table

_FINDER_LOCK = threading.Lock()


def grid_from_boxes(boxes, page_number, grid_id):
    """仅完整矩形网格且两个前导表头格高度一致时提供表头几何。"""
    def edges(values):
        groups = []
        for value in sorted(values):
            if not groups or value - groups[-1][-1] > .8:
                groups.append([value])
            else:
                groups[-1].append(value)
        return [sum(g)/len(g) for g in groups]
    xs = edges([v for b in boxes for v in (b[0], b[2])])
    ys = edges([v for b in boxes for v in (b[1], b[3])])
    rows, cols = len(ys)-1, len(xs)-1
    if not (3 <= rows <= 500 and 3 <= cols <= 80):
        return None
    cells = []
    for box in sorted({tuple(b) for b in boxes}, key=lambda b: (b[1], b[0])):
        l, r = [min(range(len(xs)), key=lambda i: abs(xs[i]-x)) for x in (box[0], box[2])]
        t, b = [min(range(len(ys)), key=lambda i: abs(ys[i]-y)) for y in (box[1], box[3])]
        cells.append({'text': '', 'row': t, 'col': l, 'rowspan': b-t, 'colspan': r-l})
    try:
        validate_table({'row_count': rows, 'col_count': cols, 'cells': cells})
    except PipelineError:
        return None
    if sum(c['rowspan']*c['colspan'] for c in cells) != rows*cols:
        return None
    lead = [c for c in cells if c['row'] == 0 and c['col'] in {0, 1} and c['colspan'] == 1]
    if len(lead) != 2 or lead[0]['rowspan'] != lead[1]['rowspan'] or not 1 <= lead[0]['rowspan'] <= 6:
        return None
    header = lead[0]['rowspan']
    if any(c['row'] < header < c['row']+c['rowspan'] for c in cells):
        return None
    notes = 0
    for row in range(rows-1, header-1, -1):
        band = [c for c in cells if c['row'] <= row < c['row']+c['rowspan']]
        if len(band) != 1 or band[0]['colspan'] != cols or band[0]['rowspan'] != 1:
            break
        notes += 1
    if rows-header-notes < 1:
        return None
    headers = [c for c in cells if c['row'] < header]
    return {'grid_id': grid_id, 'page': page_number, 'row_count': rows, 'col_count': cols,
            'header_rows': header, 'note_rows': notes,
            'bbox': [xs[0], ys[0], xs[-1], ys[-1]], 'source': 'pdf_vector_cells',
            'header_cells': [{'grid_cell_id': f'h{i}', **{k: c[k] for k in ('row', 'col', 'rowspan', 'colspan')}}
                             for i, c in enumerate(headers)]}


def vector_grids(pdf_page, forward, page_number):
    # 几何先经过与页面相同的旋转/缩放，不能使用转置前的find_tables行列数。
    from .views import transform_box
    grids = []
    # TableFinder使用模块级中间状态；并发文档只在查找及复制几何时互斥。
    with _FINDER_LOCK:
        tables = [[tuple(box) for box in table.cells if box] for table in pdf_page.find_tables().tables]
    for cells in tables:
        boxes = [transform_box(box, forward) for box in cells]
        if boxes and (grid := grid_from_boxes(boxes, page_number, 'pending')):
            grids.append(grid)
    grids.sort(key=lambda g: (g['bbox'][1], g['bbox'][0]))
    for i, grid in enumerate(grids):
        grid['grid_id'] = f'p{page_number}-g{i}'
    return grids
