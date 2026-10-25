"""简单三线表的本地计数提示；只用于有文本层、空白左上角的完整转正视图。"""
import numpy as np
from PIL import Image

from .views import transform_box


def simple_grid_hint(page, geometry=False):
    if not page.get('view_to_page') or not page.get('pdf_words'):
        return None
    import cv2
    with Image.open(page['image']) as image:
        gray = np.asarray(image.convert('L'))
    h, w = gray.shape
    mask = np.uint8(gray < 110) * 255
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, max(20, int(w * .7))), np.uint8))
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rules = sorted((y + height/2, x, x+width) for c in contours
                   for x, y, width, height in [cv2.boundingRect(c)] if width >= .7*w and height <= max(6, .02*h))
    if len(rules) != 3:
        return None
    top, middle, bottom = [r[0] for r in rules]
    if middle-top < 8 or bottom-middle < 20:
        return None
    inverse = np.linalg.inv(np.asarray(page['view_to_page']))
    words = [{'text': item['text'], 'bbox': transform_box(item['bbox'], inverse)} for item in page['pdf_words']]
    words = [v for v in words if 0 <= (v['bbox'][0]+v['bbox'][2])/2 <= w
             and 0 <= (v['bbox'][1]+v['bbox'][3])/2 <= h]
    headers = [v for v in words if top < (v['bbox'][1]+v['bbox'][3])/2 < middle]
    if not headers:
        return None
    height = float(np.median([v['bbox'][3]-v['bbox'][1] for v in headers]))
    first = min(v['bbox'][0] for v in headers)
    left = max(r[1] for r in rules)
    if first-left < .08*w:
        return None  # 不是明确的空白角格，无法可靠分离行标签列。
    # 多层表头不能简单按一行计数：各水平文字组的首行须顶对齐。
    groups = []
    for word in sorted(headers, key=lambda v: v['bbox'][0]):
        x0, y0, x1, _ = word['bbox']
        hits = [g for g in groups if x0 <= g[1]+height*.25 and x1 >= g[0]-height*.25]
        for group in hits:
            groups.remove(group)
            x0, x1, y0 = min(x0, group[0]), max(x1, group[1]), min(y0, group[2])
        groups.append((x0, x1, y0))
    if max(g[2] for g in groups)-min(g[2] for g in groups) > height*.6:
        return None
    labels = [v for v in words if middle < (v['bbox'][1]+v['bbox'][3])/2 < bottom
              and left-height*.5 <= v['bbox'][0] and v['bbox'][2] < first-height*.15]
    lines = []
    for word in sorted(labels, key=lambda v: ((v['bbox'][1]+v['bbox'][3])/2, v['bbox'][0])):
        center = (word['bbox'][1]+word['bbox'][3])/2
        if not lines or abs(center-lines[-1][0]) > height*.5:
            lines.append((center, [word]))
        else:
            lines[-1][1].append(word)
    if not 2 <= len(lines) <= 200:
        return None
    if any(b[0]-a[0] < height*1.1 for a, b in zip(lines, lines[1:])):
        return None  # 疑似同一行标签换行，保守不给计数。
    if any(min(v['bbox'][0] for v in line[1])-left > .04*w for line in lines):
        return None
    row_labels = [' '.join(v['text'] for v in sorted(line[1], key=lambda v: v['bbox'][0])) for line in lines]
    result = {'source': 'local_three_rule_text_grid', 'row_count': 1+len(lines),
            'row_breakdown': {'header': 1, 'body': len(lines), 'notes': 0}, 'body_row_labels': row_labels,
            'basis': '三条长横线界定表头和数据区；按左侧标签行计数，横线外标题/脚注排除',
            'heuristic': True}
    if geometry:
        # 仅本地定位器请求几何信息，不把词坐标重复发送给模型。
        result['geometry'] = {'headers': headers, 'words': words, 'rows': [
            {'text': text, 'bbox': [min(v['bbox'][0] for v in line[1]), min(v['bbox'][1] for v in line[1]),
                                    max(v['bbox'][2] for v in line[1]), max(v['bbox'][3] for v in line[1])]}
            for text, line in zip(row_labels, lines)]}
    return result
