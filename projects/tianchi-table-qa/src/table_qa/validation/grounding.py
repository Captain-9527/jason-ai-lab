"""本地检查定位声明、逐项范围、数字证据及整表尺寸；不调用复核模型。"""
import re
from decimal import ROUND_HALF_UP, InvalidOperation
from .answer import answer_contract, number, width_text
from .structure_scope import validate_structure_scope
from ..schemas import PipelineError
from .numeric_locale import normalize_numeric_text


def _numeric_key(value, numeric_format='standard'):
    try:
        text = normalize_numeric_text(width_text(value).strip(), numeric_format)
        if text.endswith('%'):
            return number(text[:-1]) / 100
        if text.startswith('(') and text.endswith(')'):
            return -number(text[1:-1])
        return number(text)
    except ValueError:
        return None


def validate_prior_array(q, raw, pages):
    """只拦截明确把正文前值替换成同段调整后值，不推测缺失答案或单位。"""
    if not isinstance(raw.get('answer'), list) or not re.search(r'前值', q.question):
        return
    metric = re.search(r'([\u4e00-\u9fff]+)前值', q.question)
    if not metric:
        return
    number = r'[+-]?\d+(?:\.\d+)?[%％]?'
    series = number + r'(?:/' + number + r'){1,}'
    pattern = re.compile(re.escape(metric[1]) + r'(?:分别)?为(' + series +
                         r')(?:亿元|百万元|万元|元)?[（(]前值为(' + series + r')')
    pairs = set()
    for page in pages:
        context = page.get('outside_table_context')
        texts = [v for v in context.values() if isinstance(v, str)] if isinstance(context, dict) else []
        texts.append(''.join(w['text'] for w in page.get('pdf_words', [])))
        for text in texts:
            for new, old in pattern.findall(re.sub(r'\s+', '', text)):
                pairs.add((new, old))
    if len(pairs) != 1:
        return
    new, old = next(iter(pairs))
    candidate = [_numeric_key(v) for v in raw['answer']]
    new_values = [_numeric_key(v) for v in new.split('/')]
    old_values = [_numeric_key(v) for v in old.split('/')]
    # 允许识别表格相对正文的十进制单位差；只识别“拿了新值”的明确冲突。
    if None not in candidate and any(candidate == [v * scale for v in old_values]
                                     for scale in (1, 10, 100, 1000, 10000)):
        return
    for scale in (1, 10, 100, 1000, 10000):
        try:
            same_new = (None not in candidate and len(candidate) == len(new_values)
                        and all((a / scale).quantize(v, rounding=ROUND_HALF_UP) == v
                                for a, v in zip(candidate, new_values)))
        except InvalidOperation:
            same_new = False
        if same_new and new_values != old_values:
            raise PipelineError('题目要求前值，但答案对应PDF正文中明确标记的调整后值；不能以新值替代前值')


def validate_full_row_label(q, answer, pages):
    """仅拦截明确整行提取丢掉PDF可见行名；不补造值或猜测完整列数。"""
    if (q.question_type != 'extract' or answer_contract(q) != 'json_array'
            or not isinstance(answer, list) or not answer
            or not all(_numeric_key(v) is not None for v in answer)
            or not re.search(r'提取', q.question)):
        return
    label = re.search(r'\b([A-Za-z][A-Za-z0-9_-]*)\s*行[。！？?\s]*$', q.question)
    def visible(page, word):
        area, box = page.get('view_bbox'), word.get('bbox')
        return (page.get('table_focused') and area and box
                and area[0] <= (box[0]+box[2])/2 <= area[2]
                and area[1] <= (box[1]+box[3])/2 <= area[3])
    if label and any(w['text'].casefold() == label[1].casefold() and visible(page, w)
                     for page in pages for w in page.get('pdf_words', [])):
        raise PipelineError('整行提取遗漏PDF可见行标签：不能只返回数值；指定列提取不受此检查影响')


def local_checks(q, candidate, pages, cfg):
    issues = []
    grounding = candidate.get('grounding') or {}
    if not isinstance(grounding, dict) or any(not isinstance(grounding.get(k), str) or not grounding[k].strip()
            for k in ('table', 'rows', 'columns', 'scope')):
        issues.append('缺少目标表/行/列/范围定位声明')
        grounding = {}
    answer = candidate['answer']
    if not candidate.get('evidence'):
        issues.append('没有有效定位框')
    if candidate.get('computation_checked') and candidate.get('evidence'):
        checks = []
        for evidence in candidate['evidence']:
            page = next((p for p in pages if p['page'] == evidence['page']), None)
            if not page or not page.get('pdf_vector_grids'):
                continue
            a = evidence['bbox']
            checks.append(any(max(a[0],g['bbox'][0]) < min(a[2],g['bbox'][2])
                              and max(a[1],g['bbox'][1]) < min(a[3],g['bbox'][3])
                              for g in page['pdf_vector_grids']))
        if checks and not any(checks):
            issues.append('计算证据未覆盖任何PDF矢量表格；复算正确不能证明取值正确')
    if q.question_type == 'structure':
        check = candidate.get('full_table_check') or {}
        counts = check.get('row_breakdown', {}) if isinstance(check, dict) else {}
        valid = (isinstance(check, dict) and check.get('scope') == 'full_table'
                 and all(type(check.get(k)) is int and check[k] == answer[k] for k in ('row_count', 'col_count'))
                 and type(check.get('page')) is int and check['page'] in {p['page'] for p in pages}
                 and isinstance(check.get('basis'), str) and check['basis'].strip()
                 and isinstance(counts, dict) and all(type(counts.get(k)) is int and counts[k] >= 0 for k in ('header', 'body', 'notes'))
                 and sum(counts[k] for k in ('header', 'body', 'notes')) == answer['row_count'])
        if not valid:
            issues.append('缺少整表尺寸及表头/数据/注释行数分解')
        else:
            try:
                validate_structure_scope(q, answer, check)
            except PipelineError as exc:
                issues.append(str(exc))
    items = candidate.get('items') or []
    if not isinstance(items, list):
        items = []
        issues.append('逐项来源不是数组')
    if isinstance(answer, list) and q.question_type != 'structure':
        if len(items) != len(answer) or any(not isinstance(item, dict) or str(item.get('value')) != str(value)
                or any(not isinstance(item.get(k), str) or not item[k].strip() for k in ('row', 'column', 'level'))
                for item, value in zip(items, answer)):
            issues.append('列表未按输出顺序提供等长逐项来源（不能按相同金额去重）')
    if q.route in {'calculation', 'filter', 'enumeration'} or isinstance(answer, list):
        scope = candidate.get('scope_check') or {}
        if not isinstance(scope, dict) or scope.get('complete') is not True or not isinstance(scope.get('excluded'), list):
            issues.append('缺少完整候选范围及排除项声明')
    if (cfg['processing'].get('verify_pdf_evidence', False) and answer_contract(q) == 'number'
            and _numeric_key(answer) is not None and not candidate.get('computation_checked')):
        lookup = {p['page']: p for p in pages}
        text_pages = [e for e in candidate.get('evidence', []) if lookup[e['page']].get('pdf_words')]
        if text_pages:
            found = False
            matched = []
            for e in text_pages:
                l, t, r, b = e['bbox']
                for word in lookup[e['page']]['pdf_words']:
                    x0, y0, x1, y1 = word['bbox']
                    # 证据需覆盖词中心，避免擦边框把相邻行算作证据。
                    if (l <= (x0+x1)/2 <= r and t <= (y0+y1)/2 <= b
                            and _numeric_key(word['text'], candidate.get('numeric_format', 'standard')) == _numeric_key(answer)):
                        found = True
                        matched.append((e['page'], word['bbox']))
            if not found:
                issues.append('证据框内PDF文字未支持答案数字；保留候选但不判定成功')
            for name, field in [('row', 'rows'), ('column', 'columns')]:
                anchor = (candidate.get('anchors') or {}).get(name)
                if not anchor or anchor.get('page') not in lookup:
                    issues.append(f'缺少{name}标签锚点，无法校验行列归属')
                    continue
                l, t, r, b = anchor['bbox']
                text = ' '.join(w['text'] for w in lookup[anchor['page']].get('pdf_words', [])
                                if l <= (w['bbox'][0]+w['bbox'][2])/2 <= r
                                and t <= (w['bbox'][1]+w['bbox'][3])/2 <= b).casefold()
                tokens = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', grounding.get(field, '').casefold())
                if not tokens or any(token not in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text) for token in tokens):
                    issues.append(f'{name}锚点文字与声明标签不一致')
            anchors = candidate.get('anchors') or {}
            if found and all(k in anchors for k in ('row', 'column')):
                row, col = anchors['row'], anchors['column']
                def overlap(a, b, axis):
                    return max(a[axis], b[axis]) < min(a[axis+2], b[axis+2])
                aligned = any(page == row['page'] == col['page'] and (
                    (overlap(row['bbox'], box, 1) and overlap(col['bbox'], box, 0)) or
                    (overlap(row['bbox'], box, 0) and overlap(col['bbox'], box, 1))) for page, box in matched)
                if not aligned:
                    issues.append('答案数字与行列锚点不在同一网格交点，可能串行或串列')
    facts = candidate.get('facts') or []
    if not isinstance(facts, list):
        facts = []
        issues.append('跨题事实必须为数组')
    facts = list(facts)
    clean = []
    # 数组逐项与计算操作数也是原文声明，不能只比较最终标量/合计。
    for source in (candidate.get('items'), candidate.get('source_records')):
        if isinstance(source, list) and grounding:
            facts += [{**item, 'table': grounding['table']} for item in source
                      if isinstance(item, dict) and item.get('value') is not None
                      and all(isinstance(item.get(k), str) and item[k].strip() for k in ('row', 'column'))]
    for f in facts:
        if not isinstance(f, dict) or any(not isinstance(f.get(k), str) or not f[k].strip() for k in ('table', 'row', 'column')) or f.get('value') is None:
            issues.append('跨题事实缺少表/行/列/值')
            continue
        clean.append({k: f.get(k, '') for k in ('table', 'row', 'column', 'period', 'value')})
        if 'numeric_format' in f:
            clean[-1]['numeric_format'] = f['numeric_format']
    # 单值提取自动把定位与答案作为一个声明；后续跨题校验只标记冲突，不自动改值。
    if (grounding and not isinstance(answer, (dict, list)) and q.route not in {'calculation', 'filter'}
            and not candidate.get('items') and not candidate.get('source_records')):
        clean.append({'table': grounding['table'], 'row': grounding['rows'], 'column': grounding['columns'],
                      'period': grounding.get('period', ''), 'value': answer})
    return list(dict.fromkeys(issues)), clean
