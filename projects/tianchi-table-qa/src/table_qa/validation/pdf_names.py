"""名称极值题的PDF原行名核对；不模糊纠错、不改写模型答案。"""
from ..processing.grid import simple_grid_hint
from ..schemas import PipelineError


def name_key(text):
    return ' '.join(text.split()).casefold()


def pdf_row_name_hints(pages):
    if len(pages) != 1 or not pages[0].get('table_focused'):
        return []
    hint = simple_grid_hint(pages[0])
    if not hint:
        return []
    labels = hint['body_row_labels']
    if len({name_key(v) for v in labels}) != len(labels):
        return []  # 重复行名不能据此确认实体。
    return [{'page': pages[0]['page'], 'source': hint['source'], 'row_labels': labels,
             'scope': '已聚焦简单三线表的完整行名；逐字核对，不是数值识别或标准答案'}]


def validate_pdf_name_labels(plans, hints, records=None):
    if len(hints) != 1:
        return None
    source = {name_key(v) for v in hints[0]['row_labels']}
    labels = [label for plan in plans if plan.get('op') in {'argmax', 'argmin', 'select'}
              for label in plan.get('labels', [])]
    if labels and isinstance(records, list):
        labels = [r.get('label', r.get('row')) for r in records]
    if not labels:
        return None
    if any(not isinstance(v, str) or name_key(v) not in source for v in labels):
        raise PipelineError('名称候选与PDF完整行名不一致：可能漏字/改拼写；不模糊纠错或自动替换')
    return {'source': hints[0]['source'], 'page': hints[0]['page'],
            'checked_label_count': len(labels), 'source_row_count': len(source),
            'status': 'literal_names_supported'}
