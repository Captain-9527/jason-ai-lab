"""仅核验双分组、重复单词子表头的PDF数字数组；歧义时不猜布局。"""
import re
import numpy as np

from .grounding import _numeric_key
from .pdf_localization import _columns, _key
from ..schemas import PipelineError


def validate_grouped_pdf_array(q, raw, pages):
    answer, items = raw.get('answer'), raw.get('items')
    if (q.question_type != 'extract' or q.answer_format != 'json_array' or len(pages) != 1
            or not isinstance(answer, list) or not answer or not isinstance(items, list)
            or len(items) != len(answer) or any(_numeric_key(v) is None for v in answer)):
        return None
    page = pages[0]
    if (not page.get('table_focused') or not page.get('pdf_layout_hint')
            or not np.allclose(np.asarray(page['view_to_page'])[:2, :2], np.eye(2))):
        return None
    frame = page['view_bbox']
    rules = sorted(r[1] for r in page.get('pdf_horizontal_rules', [])
                   if frame[1] < r[1] < frame[3] and r[2]-r[0] > (frame[2]-frame[0])*.9)
    if len(rules) < 3:
        return None
    words = [w for w in page.get('pdf_words', []) if frame[0] <= (w['bbox'][0]+w['bbox'][2])/2 <= frame[2]
             and rules[0] < (w['bbox'][1]+w['bbox'][3])/2 < rules[-1]]
    headers = [w for w in words if (w['bbox'][1]+w['bbox'][3])/2 < rules[1]]
    if not headers:
        return None
    font = float(np.median([w['bbox'][3]-w['bbox'][1] for w in headers]))
    lines = []
    for word in sorted(headers, key=lambda w: (w['bbox'][1]+w['bbox'][3])/2):
        center = (word['bbox'][1]+word['bbox'][3])/2
        if not lines or center-lines[-1][0] > font*.6:
            lines.append((center, [word]))
        else:
            lines[-1][1].append(word)
    if len(lines) != 2:
        return None
    parents = sorted(_columns(lines[0][1]), key=lambda c: c['bbox'][0])
    leaves = sorted((w for w in lines[1][1] if re.fullmatch(r'[A-Za-z][A-Za-z0-9]*%?', w['text'])),
                    key=lambda w: w['bbox'][0])
    repeats = [n for n in range(2, len(leaves)//2+1)
               if [w['text'] for w in leaves[-2*n:-n]] == [w['text'] for w in leaves[-n:]]]
    if len(parents) != 2 or len(repeats) != 1:
        return None
    n = repeats[0]
    leaves = leaves[-2*n:]
    centers = [(w['bbox'][0]+w['bbox'][2])/2 for w in leaves]
    if any(not centers[i*n] <= (p['bbox'][0]+p['bbox'][2])/2 <= centers[(i+1)*n-1]
           for i, p in enumerate(parents)):
        return None
    question = _key(q.question)
    def aliases(parent):
        return [_key(s) for s in re.split(r'[()（）]', parent['text']) if _key(s)]
    selected = [i for i,p in enumerate(parents) if any(a in question for a in aliases(p))]
    if len(selected) != 1:
        return None
    group = selected[0]
    body = [w for w in words if (w['bbox'][1]+w['bbox'][3])/2 > rules[1]]
    rows = []
    for w in sorted(body, key=lambda w: (w['bbox'][1]+w['bbox'][3])/2):
        y = (w['bbox'][1]+w['bbox'][3])/2
        if not rows or y-rows[-1][0] > font*.6:
            rows.append((y, [w]))
        else:
            rows[-1][1].append(w)
    candidates = []
    for y, row in rows:
        label = ' '.join(w['text'] for w in sorted(row, key=lambda w:w['bbox'][0])
                         if (w['bbox'][0]+w['bbox'][2])/2 < centers[0]-font
                         and _numeric_key(w['text']) is None)
        if _key(label) and _key(label) in question:
            candidates.append((label, row))
    if len(candidates) != 1:
        return None
    label, row = candidates[0]
    checked = []
    for value, item in zip(answer, items):
        if (not isinstance(item, dict) or _key(label) not in _key(str(item.get('row','')))
                or not any(a in _key(str(item.get('column',''))) for a in aliases(parents[group]))):
            return None
        tokens = re.findall(r'[A-Za-z][A-Za-z0-9]*%?', item.get('column',''))
        columns = [i for i in range(group*n, (group+1)*n) if leaves[i]['text'] in tokens]
        if len(columns) != 1:
            return None
        i = columns[0]
        left = (centers[i-1]+centers[i])/2 if i else centers[i]-font
        right = (centers[i]+centers[i+1])/2 if i+1<len(centers) else frame[2]
        cells = [w for w in row if left <= (w['bbox'][0]+w['bbox'][2])/2 < right]
        if len(cells) != 1 or _numeric_key(cells[0]['text']) is None:
            return None
        if _numeric_key(cells[0]['text']) != _numeric_key(value):
            raise PipelineError('PDF分组表头的行列交点与数组值冲突：可能取了另一组同名列；不自动改值')
        checked.append({'column': leaves[i]['text'], 'source_value': cells[0]['text']})
    return {'source': 'pdf_repeated_leaf_groups', 'parent': parents[group]['text'],
            'row': label, 'items': checked, 'status': 'source_values_supported'}
