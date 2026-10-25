"""以已测得的PDF列文字核对简单表头；不猜逻辑层级，不改缓存响应。"""
from collections import Counter
from copy import deepcopy
import re
import unicodedata

from ..schemas import PipelineError


def tokens(text):
    return Counter(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', unicodedata.normalize('NFKC', text).casefold()))


def simple_pdf_column_hint(page):
    """空白角格、单层表头的简单三线表；不推广到合并表头。"""
    from ..processing.grid import simple_grid_hint
    from .pdf_localization import _columns
    if not page.get('table_focused'):
        return None
    hint = simple_grid_hint(page, geometry=True)
    if not hint:
        return None
    columns = _columns(hint['geometry']['headers'])
    if len(columns) < 2:
        return None
    return {'page': page['page'], 'col_count': 1+len(columns),
            'columns': ['']+[c['text'] for c in columns], 'source': hint['source']}


def align_pdf_header_rowspans(raw, pages):
    """仅原文字/重复数值列/无内部分隔线三项吻合时恢复漏写的竖向合并。"""
    import numpy as np
    from .pdf_localization import _columns, _key
    if len(pages)!=1 or not pages[0].get('pdf_layout_hint'):
        return raw,None
    page=pages[0];answer=raw['answer'];check=raw.get('full_table_check') or {}
    levels=(check.get('row_breakdown') or {}).get('header',0)
    if (not 2<=levels<=3 or not page.get('table_focused')
            or not np.allclose(np.asarray(page['view_to_page'])[:2,:2],np.eye(2))):
        return raw,None
    cells=answer['cells'];count=answer['col_count']
    if sum(c['rowspan']*c['colspan'] for c in cells)==levels*count:
        return raw,None
    frame=page['view_bbox'];segments=page.get('pdf_horizontal_segments',[])
    rules=sorted(r[1] for r in segments if frame[1]<r[1]<frame[3]
                 and r[2]-r[0]>(frame[2]-frame[0])*.9)
    if len(rules)<3:return raw,None
    words=[w for w in page.get('pdf_words',[]) if frame[0]<=(w['bbox'][0]+w['bbox'][2])/2<=frame[2]
           and rules[0]<(w['bbox'][1]+w['bbox'][3])/2<rules[-1]]
    headers=[w for w in words if (w['bbox'][1]+w['bbox'][3])/2<rules[1]]
    if not headers:return raw,None
    font=float(np.median([w['bbox'][3]-w['bbox'][1] for w in headers]))
    numbers=[w for w in words if (w['bbox'][1]+w['bbox'][3])/2>rules[1]
             and re.fullmatch(r'[+-]?\d+(?:\.\d+)?[%％]?',w['text'])]
    clusters=[]
    for w in sorted(numbers,key=lambda w:(w['bbox'][0]+w['bbox'][2])/2):
        x=(w['bbox'][0]+w['bbox'][2])/2
        if not clusters or x-clusters[-1][0]>font:clusters.append((x,[w]))
        else:clusters[-1][1].append(w)
    minimum=max(3,check['row_breakdown']['body']//2)
    centers=[x for x,g in clusters if len(g)>=minimum]
    if len(centers)<2 or len(centers)!=count-1 or any(b-a<font*2 for a,b in zip(centers,centers[1:])):
        return raw,None
    edges=([frame[0],centers[0]-(centers[1]-centers[0])/2]+
           [(a+b)/2 for a,b in zip(centers,centers[1:])]+[frame[2]])
    source=_columns(headers);changes=[]
    covered={(r,c) for cell in cells for r in range(cell['row'],cell['row']+cell['rowspan'])
             for c in range(cell['col'],cell['col']+cell['colspan'])}
    for col in range(count):
        if all((r,col) in covered for r in range(levels)):continue
        column=[(i,c) for i,c in enumerate(cells) if c['col']<=col<c['col']+c['colspan']]
        if len(column)!=1:return raw,None
        index,cell=column[0]
        if cell['colspan']!=1 or cell['row']!=0 or cell['rowspan']!=1 or not _key(cell['text']):
            return raw,None
        groups=[g for g in source if edges[col]<=(g['bbox'][0]+g['bbox'][2])/2<edges[col+1]]
        if len(groups)!=1 or _key(groups[0]['text'])!=_key(cell['text']):return raw,None
        bottom=groups[0]['bbox'][3]
        if any(bottom+2<s[1]<rules[1]-2 and min(s[2],edges[col+1])-max(s[0],edges[col])
               >(edges[col+1]-edges[col])*.8 for s in segments):return raw,None
        changes.append({'cell_index':index,'col':col,'previous_rowspan':1,'rowspan':levels})
    if not changes:return raw,None
    value=deepcopy(raw)
    for change in changes:value['answer']['cells'][change['cell_index']]['rowspan']=levels
    return value,{'source':'pdf_header_words_and_unbroken_columns','changes':changes,
                  'original_answer':deepcopy(answer)}


def align_pdf_header(raw, layout_hints):
    check = raw.get('full_table_check') or {}
    hints = [h for h in (layout_hints or []) if h.get('page') == check.get('page')
             and h.get('physical_header_columns')]
    if len(hints) != 1:
        return raw, None
    source = hints[0]['physical_header_columns']
    answer = raw['answer']
    cells = answer['cells']
    if any(c.get('colspan') != 1 for c in cells):
        return raw, None
    shift = int(answer['col_count'] == len(source)-1 and source[0] == ''
                and not any(c['col'] == 0 and c['text'] == '' for c in cells))
    if answer['col_count']+shift != len(source):
        raise PipelineError('表头列数与PDF重复数值列/侧分组列不一致；不猜列数')
    actual = [Counter() for _ in source]
    for cell in cells:
        actual[cell['col']+shift].update(tokens(cell['text']))
    if any(actual[i] != tokens(text) for i, text in enumerate(source)):
        raise PipelineError('表头文字与PDF各物理列不一致：可能重复换行后缀、遗漏文字或串列；不自动删字')
    if not shift:
        return raw, {'source': 'pdf_physical_header_columns', 'shift': 0}
    value = deepcopy(raw)
    value['answer']['col_count'] = len(source)
    value['full_table_check']['col_count'] = len(source)
    for cell in value['answer']['cells']:
        cell['col'] += 1
    value['answer']['cells'].insert(0, {'text': '', 'row': 0, 'col': 0,
                                      'rowspan': check['row_breakdown']['header'], 'colspan': 1})
    return value, {'source': 'pdf_physical_header_columns', 'shift': 1,
                   'original_answer': deepcopy(answer), 'physical_header_columns': source,
                   'reason': '各原表头文字与PDF列逐一一致，仅缺已确认的最左侧分组角格'}
