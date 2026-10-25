"""窄范围语义冲突：只拒绝可证明的字段类型或遗漏候选，不生成替代答案。"""
import re

from ..schemas import PipelineError
from .grounding import _numeric_key


def validate_field_semantics(q, raw, pages):
    answer = raw.get('answer')
    if isinstance(answer, str):
        if re.search(r'是否[横纵]向拍摄', q.question):
            grounding = raw.get('grounding') or {}
            items = raw.get('items') or []
            labels = ([str(v) for v in grounding.values()] if isinstance(grounding, dict) else [])
            if isinstance(items, list):
                labels += [str(item.get(k, '')) for item in items if isinstance(item, dict)
                           for k in ('row', 'column', 'level')]
            if not isinstance(grounding, dict) or not re.search(
                    r'横|纵|拍摄|宽|高|landscape|portrait|orientation|width|height',
                    ' '.join(labels), re.I):
                raise PipelineError('拍摄方向题缺少图像方向依据，不能抄取表内无关是非字段')
        if '名义化学成分' in q.question and '钛' in q.question + q.table_hint:
            if re.match(r'^Tr(?=[-—–·\d\s]|$)', answer.strip()):
                raise PipelineError('钛合金名义成分须核对原文Ti元素符号，不能用非元素Tr等拼写代替')
        if re.search(r'竖排.*(?:表名|标题)', q.question):
            caption = re.match(r'\s*表\s*(\d+)', str((raw.get('grounding') or {}).get('table', '')))
            if re.match(r'\s*[A-Z]{1,8}\s*/\s*T?\s*\d', answer) or (
                    caption and not re.match(r'\s*表\s*' + re.escape(caption[1]) + r'(?!\d)', answer)):
                raise PipelineError('题目要求竖排表名/题注，答案却是标准号或与所声明表名不一致的字段')
    records = raw.get('records')
    plans = raw.get('verifications')
    if not isinstance(records, list) or not records or not isinstance(plans, list) or len(plans) != 1:
        return
    plan = plans[0]
    if plan.get('op') not in {'argmax', 'argmin'} or any(not str(r.get('value', '')).endswith('%') for r in records):
        return
    columns = {r.get('column') for r in records}
    if len(columns) != 1 or not isinstance(next(iter(columns)), str):
        return
    label = re.split(r'[>＞/|:：]', next(iter(columns)))[-1].strip()
    if not re.fullmatch(r'[A-Za-z]{2,8}', label) or label.casefold() not in q.question.casefold():
        return
    for page in pages:
        words = page.get('pdf_words', [])
        values = [w for w in words if re.fullmatch(r'[+-]?\d+(?:\.\d+)?%', w['text'])]
        if len(values) < 4:
            continue
        font = max(w['bbox'][3]-w['bbox'][1] for w in values)
        centers = [(w['bbox'][0]+w['bbox'][2])/2 for w in values]
        if max(centers)-min(centers) > font*2:
            continue
        left, right = min(w['bbox'][0] for w in values), max(w['bbox'][2] for w in values)
        top = min(w['bbox'][1] for w in values)
        head = [w for w in words if top-font*10 < (w['bbox'][1]+w['bbox'][3])/2 < top
                and left <= (w['bbox'][0]+w['bbox'][2])/2 <= right]
        # 允许原字体把短列标题逐字分词；仅唯一同列标题、紧凑数值带适用。
        lines = []
        for word in sorted(head, key=lambda w:(w['bbox'][1],w['bbox'][0])):
            y = (word['bbox'][1]+word['bbox'][3])/2
            if not lines or abs(y-lines[-1][0]) > font*.3:
                lines.append((y,[word]))
            else:
                lines[-1][1].append(word)
        matches = [''.join(w['text'] for w in sorted(line,key=lambda w:w['bbox'][0])) for _,line in lines]
        if sum(re.sub(r'[^A-Za-z]', '', text).casefold() == label.casefold() for text in matches) != 1:
            continue
        op = max if plan['op'] == 'argmax' else min
        if op(_numeric_key(w['text']) for w in values) != op(_numeric_key(r['value']) for r in records):
            raise PipelineError('极值候选遗漏同一PDF百分数列中更优的可见数值，不能只比较提供的部分记录')
