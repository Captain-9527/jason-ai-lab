"""简单三线表的确定性证据定位：只验证既有答案，绝不从附近挑另一个值。"""
import unicodedata

import numpy as np

from ..processing.grid import simple_grid_hint
from ..processing.views import transform_box
from .answer import answer_contract
from .grounding import _numeric_key


def _key(text):
    """仅作位置匹配键；不改答案原文，空白/括号/断行连字符不影响标签。"""
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def _union(words):
    return [min(w['bbox'][0] for w in words), min(w['bbox'][1] for w in words),
            max(w['bbox'][2] for w in words), max(w['bbox'][3] for w in words)]


def _columns(headers):
    """按横向区间聚合换行表头；邻列粘连时匹配失败，不模糊选取。"""
    gap = float(np.median([w['bbox'][3]-w['bbox'][1] for w in headers])) * .5
    groups = []
    for word in sorted(headers, key=lambda w: w['bbox'][0]):
        current = [word]
        while True:
            area = _union(current)
            hits = [g for g in groups if _union(g)[0] <= area[2]+gap and _union(g)[2] >= area[0]-gap]
            if not hits:
                break
            for group in hits:
                groups.remove(group)
                current.extend(group)
        groups.append(current)
    result = []
    for group in groups:
        lines = []
        for word in sorted(group, key=lambda w: (w['bbox'][1]+w['bbox'][3])/2):
            y = (word['bbox'][1]+word['bbox'][3])/2
            if not lines or abs(y-lines[-1][0]) > gap:
                lines.append((y, [word]))
            else:
                lines[-1][1].append(word)
        text = ' '.join(w['text'] for _, line in lines for w in sorted(line, key=lambda w: w['bbox'][0]))
        result.append({'text': text, 'bbox': _union(group)})
    return result


def localize_pdf_value(q, candidate, pages):
    """唯一行列交点支持同值才生成新框；所有坐标返回处理后完整页像素。"""
    if (q.question_type != 'extract' or q.route != 'lookup' or answer_contract(q) != 'number'
            or _numeric_key(candidate['answer']) is None):
        return {'status': 'not_applicable'}
    if len(pages) != 1:
        return {'status': 'skipped', 'reason': 'multiple_input_pages'}
    grounding = candidate.get('grounding')
    if not isinstance(grounding, dict) or any(not isinstance(grounding.get(k), str) or not _key(grounding[k])
                                              for k in ('rows', 'columns')):
        return {'status': 'skipped', 'reason': 'missing_labels'}
    row_key, col_key = _key(grounding['rows']), _key(grounding['columns'])
    question = _key(q.question)
    matches = []
    for page in pages:
        if not page.get('table_focused'):
            continue
        geometry = page.get('pdf_layout_geometry')
        if geometry:
            columns = geometry['columns']
            mapping = np.eye(3)
        else:
            hint = simple_grid_hint(page, geometry=True)
            if not hint:
                continue
            geometry = hint['geometry']
            columns = _columns(geometry['headers'])
            mapping = page['view_to_page']
        # 以题干和PDF的完整标签为准；模型可附带说明，但不得替换为另一行/列。
        rows = [r for r in geometry['rows'] if _key(r['text']) and _key(r['text']) in question]
        cols = [c for c in columns if _key(c['text']) and _key(c['text']) in question]
        if (len(rows) != 1 or len(cols) != 1 or _key(rows[0]['text']) not in row_key
                or _key(cols[0]['text']) not in col_key):
            continue
        row, col = rows[0], cols[0]
        words = [w for w in geometry['words']
                 if row['bbox'][1] <= (w['bbox'][1]+w['bbox'][3])/2 <= row['bbox'][3]
                 and col['bbox'][0] <= (w['bbox'][0]+w['bbox'][2])/2 <= col['bbox'][2]]
        matches.append((page, row, col, words, mapping))
    if len(matches) != 1:
        return {'status': 'skipped', 'reason': 'nonunique_or_unsupported_grid'}
    page, row, col, words, mapping = matches[0]
    # 多词数字、范围、字体编码错误均不拼接/猜测；保留原模型答案及不确定性。
    audit = {'source': 'pdf_ruled_grid' if page.get('pdf_layout_geometry') else 'pdf_text_grid',
             'page': page['page'], 'row_text': row['text'],
             'column_text': col['text'], 'observed_texts': [w['text'] for w in words]}
    if len(words) != 1 or _numeric_key(words[0]['text']) is None:
        return {**audit, 'status': 'unsupported_value'}
    if _numeric_key(words[0]['text']) != _numeric_key(candidate['answer']):
        return {**audit, 'status': 'value_mismatch'}
    def entry(area):
        return {'page': page['page'], 'bbox': transform_box(area, mapping)}
    return {**audit, 'status': 'supported', 'evidence': [entry(words[0]['bbox'])],
            'anchors': {'row': entry(row['bbox']), 'column': entry(col['bbox'])}}
