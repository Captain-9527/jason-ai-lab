"""本地PDF区域方向与文字索引；不新增模型调用，不改页面/识别缓存。"""
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image

from ..infrastructure.storage import digest, file_hash, read_json, atomic_json
from .document import rotate_quadrant


def transform_box(area, matrix):
    points = np.array([[area[0], area[1], 1], [area[2], area[1], 1],
                       [area[0], area[3], 1], [area[2], area[3], 1]]) @ np.asarray(matrix).T
    return [float(points[:, 0].min()), float(points[:, 1].min()),
            float(points[:, 0].max()), float(points[:, 1].max())]


def enrich_pages(source, pages, cfg, work, trace):
    if Path(source).suffix.lower() != '.pdf' or not any(cfg['processing'].get(k, False)
            for k in ('local_table_orientation', 'verify_pdf_evidence')):
        return pages
    from .vector_grid import vector_grids
    grid_enabled = cfg['processing'].get('vector_grid_headers', False)
    key = digest({'source': file_hash(source), 'code': file_hash(__file__),
                  'vector_grid_headers': grid_enabled,
                  'vector_grid_code': file_hash(Path(__file__).with_name('vector_grid.py')) if grid_enabled else None,
                  'pages': [(p['page'], file_hash(p['image']), p['processed_to_original']) for p in pages]})
    cache = Path(cfg['paths']['cache']) / 'views' / (key + '.json')
    if cache.exists():
        metadata = read_json(cache)
    else:
        import pymupdf
        metadata = []
        with pymupdf.open(source) as doc:
            for p in pages:
                page = doc[p['page'] - 1]
                zoom = p.get('dpi', cfg['processing']['dpi']) / 72
                rm = page.rotation_matrix
                forward = np.linalg.inv(np.asarray(p['processed_to_original'])) @ np.array(
                    [[rm.a * zoom, rm.c * zoom, rm.e * zoom],
                     [rm.b * zoom, rm.d * zoom, rm.f * zoom], [0, 0, 1]])
                words = [{'text': w[4], 'bbox': transform_box(w[:4], forward)} for w in page.get_text('words')]
                rules = []
                for drawing in page.get_drawings():
                    for item in drawing['items']:
                        if item[0] == 'l':
                            a, b = item[1], item[2]
                            bounds = [min(a.x,b.x),min(a.y,b.y),max(a.x,b.x),max(a.y,b.y)]
                        elif item[0] == 're':
                            bounds = list(item[1])
                        else:
                            continue
                        box = transform_box(bounds, forward)
                        if box[2]-box[0] >= 2 and box[3]-box[1] <= 3:
                            rules.append(box)
                # PDF常把表线画成细填充矩形，或逐单元格画相接短段；只合并同基线相接段。
                joined = []
                for box in sorted(rules,key=lambda b:((b[1]+b[3])/2,b[0])):
                    hits = [r for r in joined if abs((r[1]+r[3]-box[1]-box[3])/2)<=2
                            and r[0]<=box[2]+3 and box[0]<=r[2]+3]
                    for r in hits:
                        joined.remove(r)
                        box=[min(box[0],r[0]),min(box[1],r[1]),max(box[2],r[2]),max(box[3],r[3])]
                    joined.append(box)
                segments=[r for r in joined if r[3]-r[1]<=3]
                rules=[r for r in segments if r[2]-r[0]>=p['width']*.5]
                regions = []
                for block in page.get_text('dict').get('blocks', []):
                    for line in block.get('lines', []):
                        text = ''.join(s['text'] for s in line['spans'])
                        direction = forward[:2, :2] @ np.asarray(line['dir'])
                        angle = math.degrees(math.atan2(direction[1], direction[0]))
                        nearest = round(angle / 90) * 90
                        turn = (-nearest) % 360
                        if not text.strip() or not turn or abs(angle - nearest) > 10:
                            continue
                        box = transform_box(line['bbox'], forward)
                        # 同向、空间相邻的文字聚为一个区域，避免按整页正文投票。
                        texts = [text]
                        gap = 30 * zoom
                        while True:
                            hits = [r for r in regions if r['degrees'] == turn and not (
                                box[2] + gap < r['bbox'][0] or r['bbox'][2] + gap < box[0] or
                                box[3] + gap < r['bbox'][1] or r['bbox'][3] + gap < box[1])]
                            if not hits:
                                break
                            for r in hits:
                                regions.remove(r)
                                box = [min(box[0], r['bbox'][0]), min(box[1], r['bbox'][1]),
                                       max(box[2], r['bbox'][2]), max(box[3], r['bbox'][3])]
                                texts.append(r['text'])
                        regions.append({'bbox': box, 'degrees': turn, 'text': ' '.join(texts)})
                images = [{'bbox': transform_box(info['bbox'], forward),
                           'native_width': info['width'], 'native_height': info['height']}
                          for info in page.get_image_info()]
                metadata.append({'pdf_vector_grids':vector_grids(page,forward,p['page']) if grid_enabled else [],
                                 'pdf_words': words, 'pdf_horizontal_rules': rules,
                                 'pdf_horizontal_segments':segments, 'pdf_rasters': images,
                                 'orientation_regions': [r for r in regions if len(r['text']) >= 40]})
        atomic_json(cache, metadata)
    return [{**p, **extra} for p, extra in zip(pages, metadata)]


def ruled_table_bounds(image, words, view_to_page):
    """仅接受完整、对齐的单张三线表；不把段落横线或多张表拼成一个框。"""
    import cv2
    gray = np.asarray(image.convert('L'))
    h, w = gray.shape
    mask = np.uint8(gray < 110) * 255
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                             np.ones((1, max(20, int(w * .65))), np.uint8))
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rules = sorted((y, x, x + width, y + height) for c in contours
                   for x, y, width, height in [cv2.boundingRect(c)]
                   if width >= w * .65 and height <= max(6, h * .02))
    if len(rules) != 3:
        return None
    top, middle, bottom = rules
    left, right = min(v[1] for v in rules), max(v[2] for v in rules)
    if (max(v[1] for v in rules) - left > w * .03
            or right - min(v[2] for v in rules) > w * .03
            or middle[0] - top[3] < 8 or bottom[0] - middle[3] < 20):
        return None
    inverse = np.linalg.inv(np.asarray(view_to_page))
    inside = [transform_box(v['bbox'], inverse) for v in words]
    header = [v for v in inside if left <= (v[0]+v[2])/2 <= right
              and top[3] < (v[1]+v[3])/2 < middle[0]]
    body = [v for v in inside if left <= (v[0]+v[2])/2 <= right
            and middle[3] < (v[1]+v[3])/2 < bottom[0]]
    if len(header) < 3 or len(body) < 6:
        return None
    # 表头应横跨多个位置，数据区应有多行；纯正文/装饰线不据此裁剪。
    font = float(np.median([v[3]-v[1] for v in header + body]))
    if font <= 0 or max(v[0] for v in header)-min(v[0] for v in header) < (right-left)*.35:
        return None
    centers = sorted((v[1]+v[3])/2 for v in body)
    rows = [centers[0]]
    for center in centers[1:]:
        if center - rows[-1] > font * .7:
            rows.append(center)
    if len(rows) < 3:
        return None
    # 不能截断穿越表线的文字（可能是边界选错或同一行的多表）。
    if any(v[0] < left-font*.5 or v[2] > right+font*.5 for v in header+body):
        return None
    margin = max(3, math.ceil(font * .35))
    return [max(0, left-margin), max(0, top[0]-margin),
            min(w, right+margin), min(h, bottom[3]+margin)]


def region_view(p, region):
    l, t, r, b = region['bbox']
    area = [max(0, math.floor(l - 16)), max(0, math.floor(t - 16)),
            min(p['width'], math.ceil(r + 16)), min(p['height'], math.ceil(b + 16))]
    with Image.open(p['image']) as im:
        image, inverse = rotate_quadrant(im.crop(area), region['degrees'])
    offset = np.array([[1, 0, area[0]], [0, 1, area[1]], [0, 0, 1]])
    return image, offset @ inverse


def outside_table_context(words, matrix, bounds, size):
    """保留表外标题/单位/脚注的文本上下文，明确不作为单元格或逻辑行。"""
    inverse = np.linalg.inv(matrix)
    above, below = [], []
    for word in words:
        box = transform_box(word['bbox'], inverse)
        x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
        if 0 <= x <= size[0] and 0 <= y <= size[1]:
            if y < bounds[1]:
                above.append((y, box[0], box[3]-box[1], word['text']))
            elif y > bounds[3]:
                below.append((y, box[0], box[3]-box[1], word['text']))
    result = {}
    for name, values in [('above', above), ('below', below)]:
        if not values:
            continue
        lines = []
        tolerance = float(np.median([v[2] for v in values])) * .5
        for word in sorted(values):
            if not lines or word[0] - lines[-1][0][0] > tolerance:
                lines.append([word])
            else:
                lines[-1].append(word)
        result[name] = '\n'.join(' '.join(v[3] for v in sorted(line, key=lambda v: v[1]))
                                 for line in lines)[:1000]
    return result


def table_number_target(q):
    """题干中的单表编号优先；范围提示不等于该范围的首张表。"""
    def numbers(text):
        return {m[1] for m in re.finditer(r'\bTable\s*(\d+)(?!\d)', text, re.I)
                if not re.match(r'\s*[-–—~～至到]\s*\d', text[m.end():])}
    explicit = numbers(q.question)
    if len(explicit) > 1:
        return None, True
    if explicit:
        return next(iter(explicit)), False
    hinted = numbers(q.table_hint)
    return (next(iter(hinted)), False) if len(hinted) == 1 else (None, bool(hinted))


def upright_ruled_view(q, page, work, trace):
    """按唯一题注分隔表框，再验证横线；多目标或不可靠边界不裁剪。"""
    words = page.get('pdf_words', [])
    target, ambiguous = table_number_target(q)
    if ambiguous:
        return None
    captions = []
    for word in words:
        if not re.fullmatch(r'Table', word['text'], re.I):
            continue
        box = word['bbox']
        line = sorted((w for w in words if abs(w['bbox'][1]-box[1]) < (box[3]-box[1])*.4
                       and w['bbox'][0] >= box[0]), key=lambda w: w['bbox'][0])
        preceding = any(w['bbox'][2] <= box[0] and abs(w['bbox'][1]-box[1]) < (box[3]-box[1])*.4
                        for w in words)
        match = re.match(r'Table\s+(\d+)(?:\s*[|:：]|\s*$)', ' '.join(w['text'] for w in line), re.I)
        if match and not preceding:
            captions.append((box[1], match[1]))
    captions.sort()
    selected = [c for c in captions if target is None or c[1] == target]
    if len(selected) != 1:
        return None
    caption_top, caption_number = selected[0]
    next_top = min((y for y, _ in captions if y > caption_top), default=page['height'])
    groups = []
    for rule in page.get('pdf_horizontal_rules', []):
        if not caption_top < rule[1] < next_top:
            continue
        group = next((g for g in groups if abs(g[0][0]-rule[0]) < page['width']*.01
                      and abs(g[0][2]-rule[2]) < page['width']*.01), None)
        if group is None:
            groups.append([rule])
        else:
            group.append(rule)
    groups = [sorted(g, key=lambda b: b[1]) for g in groups if len(g) >= 3]
    # 小计线可比外框短：只接受唯一包住其余横线组的外框，不按面积猜。
    frames = [g for g in groups if all(
        g[0][0]-3 <= min(r[0] for r in other)
        and max(r[2] for r in other) <= g[0][2]+3
        and g[0][1] <= other[0][1] and other[-1][3] <= g[-1][3]
        for other in groups)]
    if len(frames) != 1:
        return None
    rules = frames[0]
    if any(b[1]-a[1] > page['height']*.25 for a, b in zip(rules, rules[1:])):
        return None
    left, right = min(b[0] for b in rules), max(b[2] for b in rules)
    top, middle, bottom = rules[0][1], rules[1][1], rules[-1][3]
    inside = [w for w in words if left <= (w['bbox'][0]+w['bbox'][2])/2 <= right
              and top < (w['bbox'][1]+w['bbox'][3])/2 < bottom]
    if len(inside) < 12 or bottom-middle < 50:
        return None
    # 合并两张对齐表会把第二份题注包入；拒绝而不是按大框猜一个表。
    if any(re.fullmatch(r'Table|表', w['text'], re.I) for w in inside):
        return None
    # 题注可以与正文左对齐，而窄表整体缩进；编号已在同页题注中唯一匹配。
    if top-caption_top > page['height']*.15:
        return None
    header = [w for w in inside if (w['bbox'][1]+w['bbox'][3])/2 < middle]
    edge = [w for w in inside if (w['bbox'][0]+w['bbox'][2])/2 >= right-(right-left)*.12
            and (w['bbox'][1]+w['bbox'][3])/2 > middle]
    numeric_rows = [w for w in inside if (w['bbox'][0]+w['bbox'][2])/2 >= left+(right-left)*.5
                    and (w['bbox'][1]+w['bbox'][3])/2 > middle
                    and re.fullmatch(r'[+-]?\d+(?:\.\d+)?[%％]?',w['text'])]
    # 最右格可能为空；跨多个数值列联合取基线，避免丢掉仍有其他值的数据行。
    edge = numeric_rows if len(numeric_rows)>=2 else edge
    if not header or len(edge) < 2:
        return None
    font = float(np.median([min(w['bbox'][2]-w['bbox'][0], w['bbox'][3]-w['bbox'][1]) for w in header]))
    centers = []
    for value in sorted((w['bbox'][1]+w['bbox'][3])/2 for w in edge):
        if not centers or value-centers[-1] > font*.6:
            centers.append(value)
    body_rows = [' '.join(w['text'] for w in sorted(
        (w for w in inside if abs((w['bbox'][1]+w['bbox'][3])/2-y) <= font*.6
         and w['bbox'][3]-w['bbox'][1] <= font*1.8), key=lambda w: w['bbox'][0])) for y in centers]
    hint = {'source': 'pdf_aligned_rules_text', 'body_text_row_count': len(centers),
            'body_rows': body_rows,
            'header_tokens': [[w['text'], round(w['bbox'][0]-left), round(w['bbox'][1]-top)] for w in header],
            'header_token_coordinates': '相对表格左上角的像素偏移，仅供判断左右/换行，不是逻辑row/col',
            'leading_group_column_visible': any(w['bbox'][2] < min(v['bbox'][0] for v in header)-font*.2
                                                for w in inside if (w['bbox'][1]+w['bbox'][3])/2 > middle),
            'heuristic': True,
            'basis': 'body_rows逐行核对后才可显式引用；换行/合并不等于逻辑行。左侧分组列即使表头空白仍占列，空白在最左不是最右。'}
    layout = pdf_header_columns(inside, header, middle, font, len(centers), geometry=True)
    geometry = None
    if layout:
        hint['physical_header_columns'] = [col['text'] for col in layout['columns']]
        labels = []
        for y in centers:
            items = [w for w in inside if abs((w['bbox'][1]+w['bbox'][3])/2-y) <= font*.6
                     and layout['row_label_band'][0] <= (w['bbox'][0]+w['bbox'][2])/2 <= layout['row_label_band'][1]
                     and w['bbox'][3]-w['bbox'][1] <= font*1.8]
            if not items:
                continue
            labels.append({'text': ' '.join(w['text'] for w in sorted(items, key=lambda w: w['bbox'][0])),
                           'bbox': [min(w['bbox'][0] for w in items), min(w['bbox'][1] for w in items),
                                    max(w['bbox'][2] for w in items), max(w['bbox'][3] for w in items)]})
        geometry = {'rows': labels, 'columns': layout['columns'][1:], 'words': inside,
                    'coordinate_space': 'processed_page'}
    area = [max(0, math.floor(left-6)), max(0, math.floor(top-6)),
            min(page['width'], math.ceil(right+6)), min(page['height'], math.ceil(bottom+6))]
    target_path = Path(work)/'views'/(digest(['upright', area, file_hash(page['image'])])[:20]+'.png')
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists():
        with Image.open(page['image']) as image:
            image.crop(area).save(target_path)
    context = outside_table_context(words, np.eye(3), area, (page['width'], page['height']))
    result = {**page, 'image': str(target_path), 'width': area[2]-area[0], 'height': area[3]-area[1],
              'view_to_page': [[1, 0, area[0]], [0, 1, area[1]], [0, 0, 1]], 'view_bbox': area,
              'table_focused': True, 'outside_table_context': context,
              'selection_basis': 'unique_captioned_ruled_table', 'pdf_layout_hint': hint}
    if geometry:
        result['pdf_layout_geometry'] = geometry  # 仅本地校验，不传给模型。
    trace.emit('local_table_view', page=page['page'], bbox=area, degrees=0, width=result['width'],
               height=result['height'], table_focused=True, selection_basis=result['selection_basis'])
    return result


def pdf_header_columns(words, headers, body_top, font, row_count, geometry=False):
    """仅重复数值列充分、表头清晰的布局给出物理列文字，复杂合并不猜。"""
    numeric = [w for w in words if (w['bbox'][1]+w['bbox'][3])/2 > body_top
               and re.fullmatch(r'[+-]?\d+(?:\.\d+)?[%％]?', w['text'])]
    clusters = []
    for word in sorted(numeric, key=lambda w: (w['bbox'][0]+w['bbox'][2])/2):
        x = (word['bbox'][0]+word['bbox'][2])/2
        if not clusters or x-clusters[-1][0] > font:
            clusters.append((x, [word]))
        else:
            clusters[-1][1].append(word)
    clusters = [c for c in clusters if len(c[1]) >= max(3, row_count//2)]
    if len(clusters) < 2 or any(b[0]-a[0] < font*2 for a, b in zip(clusters, clusters[1:])):
        return None
    boundary = clusters[0][0]-(clusters[1][0]-clusters[0][0])/2
    data_headers = [[] for _ in clusters]
    left_headers = []
    for word in headers:
        x = (word['bbox'][0]+word['bbox'][2])/2
        if x < boundary:
            left_headers.append(word)
        else:
            index = min(range(len(clusters)), key=lambda i: abs(clusters[i][0]-x))
            data_headers[index].append(word)
    if not left_headers or any(not col for col in data_headers):
        return None
    def groups(items):
        result = []
        for word in sorted(items, key=lambda w: w['bbox'][0]):
            if not result or word['bbox'][0]-max(w['bbox'][2] for w in result[-1]) > font*.8:
                result.append([word])
            else:
                result[-1].append(word)
        return result
    left = groups(left_headers)
    side = [w for w in words if w['bbox'][2] < min(v['bbox'][0] for v in headers)-font*.2
            and (w['bbox'][1]+w['bbox'][3])/2 > body_top]
    if len(groups(side)) != 1:
        return None  # 没有唯一侧分组列，不尝试补角格。
    def text(items):
        return ' '.join(w['text'] for w in sorted(items, key=lambda w: (round(w['bbox'][1]/font), w['bbox'][0])))
    columns = [{'text': text(col), 'bbox': [min(w['bbox'][0] for w in col), min(w['bbox'][1] for w in col),
                                            max(w['bbox'][2] for w in col), max(w['bbox'][3] for w in col)]}
               for col in left+data_headers]
    if geometry:
        return {'columns': [{'text': '', 'bbox': None}]+columns,
                'row_label_band': [columns[0]['bbox'][0],
                                   (columns[0]['bbox'][2]+columns[1]['bbox'][0])/2 if len(left)>1 else boundary]}
    return ['']+[col['text'] for col in columns]


def captioned_raster_view(q, page, work, trace):
    """仅题注明确且唯一的嵌入图像；保留正文上下文，不冒充文字层表格。"""
    numbers = set(re.findall(r'(?:图|表|Figure|Fig\.?)\s*(\d+)', q.question+' '+q.table_hint, re.I))
    if len(numbers) != 1:
        return None
    pattern = re.compile(r'^(?:图|表|Figure|Fig\.?)\s*'+re.escape(next(iter(numbers)))+r'(?!\d)', re.I)
    captions = []
    words = page.get('pdf_words', [])
    for word in words:
        box = word['bbox']
        line = sorted((w for w in words if abs(w['bbox'][1]-box[1]) < (box[3]-box[1])*.4
                       and w['bbox'][0] >= box[0]), key=lambda w:w['bbox'][0])
        if pattern.match(' '.join(w['text'] for w in line)):
            captions.append(box)
    choices = []
    for raster in page.get('pdf_rasters', []):
        l,t,r,b = raster['bbox']
        fraction = (r-l)*(b-t)/(page['width']*page['height'])
        if (not .1 <= fraction <= .85 or r-l < page['width']*.45
                or raster['native_width'] < 300 or raster['native_height'] < 100):
            continue
        if sum(0 <= t-c[3] < page['height']*.08 and l-page['width']*.03 <= c[0] < r
               for c in captions) == 1:
            choices.append(raster)
    if len(choices) != 1:
        return None
    l,t,r,b = choices[0]['bbox']
    area = [max(0,math.floor(l)),max(0,math.floor(t)),min(page['width'],math.ceil(r)),min(page['height'],math.ceil(b))]
    target = Path(work)/'views'/(digest(['raster',area,file_hash(page['image'])])[:20]+'.png')
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        with Image.open(page['image']) as image:
            image.crop(area).save(target)
    result = {**page, 'image':str(target), 'width':area[2]-area[0], 'height':area[3]-area[1],
              'view_to_page':[[1,0,area[0]],[0,1,area[1]],[0,0,1]], 'view_bbox':area,
              'table_focused':False, 'embedded_raster':True, 'selection_basis':'unique_captioned_raster',
              'outside_table_context':outside_table_context(words,np.eye(3),area,(page['width'],page['height']))}
    trace.emit('local_table_view',page=page['page'],bbox=area,degrees=0,width=result['width'],
               height=result['height'],table_focused=False,selection_basis=result['selection_basis'])
    return result


def question_views(q, pages, cfg, work, trace):
    if cfg['processing'].get('local_table_orientation', False):
        from .caption_view import vertical_caption_view
        caption = vertical_caption_view(q, pages, work, trace, cfg['processing']['max_pixels'])
        if caption:
            return caption
    if not cfg['processing'].get('local_table_orientation', False) or q.route in {'metadata', 'visual'}:
        return pages
    if re.search(r'所有表|多张表|多表|\ball tables\b|\bmultiple tables\b', q.question, re.I):
        return pages  # 不用单表局部视图替代明确的多表范围。
    # 无可靠语义匹配时，仅对唯一的完整三线表做局部聚焦，不按面积猜目标。
    terms = set(re.findall(r'[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}', q.question.casefold())) - {'pdf', 'png', 'jpg', 'table'}
    focus = cfg['processing'].get('focus_table_content', False)
    needs_context = bool(re.search(r'脚注|注释|说明|正文|footnote|\bnotes?\b', q.question, re.I))
    result = []
    for p in pages:
        choices = [(sum(t in r['text'].casefold() for t in terms), r) for r in p.get('orientation_regions', [])]
        choices.sort(key=lambda x: x[0], reverse=True)
        matched = bool(choices and choices[0][0] >= 2
                       and (len(choices) == 1 or choices[0][0] > choices[1][0]))
        candidates = []
        generic_structure = q.question_type == 'structure' and focus and not needs_context and len(choices) == 1
        eligible = choices[:1] if matched else choices if generic_structure else []
        for _, region in eligible:
            image, matrix = region_view(p, region)
            bounds = ruled_table_bounds(image, p.get('pdf_words', []), matrix) if focus and not needs_context else None
            if matched or bounds:
                candidates.append((region, image, matrix, bounds))
        if len(candidates) != 1:
            if focus and cfg['run'].get('focus_ruled_pdf_tables', False) and not needs_context:
                upright = upright_ruled_view(q, p, work, trace)
                if upright:
                    result.append(upright)
                    continue
                raster = captioned_raster_view(q, p, work, trace)
                if raster:
                    result.append(raster)
                    continue
            trace.emit('local_table_view_fallback', page=p['page'], reason='ambiguous_or_unconfirmed_table')
            result.append(p)
            continue
        region, image, matrix, bounds = candidates[0]
        context = {}
        if bounds:
            context = outside_table_context(p.get('pdf_words', []), matrix, bounds, image.size)
            image = image.crop(bounds)
            matrix = matrix @ np.array([[1, 0, bounds[0]], [0, 1, bounds[1]], [0, 0, 1]])
        w, h = image.size
        area = transform_box([0, 0, w, h], matrix)
        target = Path(work) / 'views' / (digest([p['page'], area, region['degrees'], file_hash(p['image'])])[:20] + '.png')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            image.save(target)
        result.append({**p, 'image': str(target), 'width': w, 'height': h,
                       'view_to_page': matrix.tolist(), 'view_bbox': area,
                       'table_focused': bool(bounds),
                       'outside_table_context': context,
                       'selection_basis': 'question_match' if matched else 'unique_ruled_region'})
        trace.emit('local_table_view', page=p['page'], bbox=area, degrees=region['degrees'], width=w, height=h,
                   table_focused=bool(bounds), selection_basis=result[-1]['selection_basis'])
    return result
