"""跨题事实冲突检查：相同文档/表/行/列/期间，不按题号关联或改写答案。"""
import re
from .grounding import _numeric_key
from .task_scope import task_check_contract


def normalize(value):
    return re.sub(r'\s+', ' ', str(value)).strip().casefold()


def anchor(fact):
    table, row, column, period = (normalize(fact.get(k, '')) for k in ('table', 'row', 'column', 'period'))
    # 仅钛合金元素列：父组必须保留，绝不把“主要成分Fe”与“杂质Fe”混同。
    original = str(fact.get('column', ''))
    group = '杂质' if '杂质' in original else '主要成分' if '主要成分' in original else None
    label = re.search(r'([A-Z][a-z]?)\s*$', original)
    caption = re.match(r'表\s*(\d+)', table)
    if '钛' in table and '化学成分' in table and group and label and caption:
        table, column = '表'+caption[1], group+'>'+label[1].casefold()
    return table, row, column, period


def check_document_consistency(questions, results):
    index, conflicts = {}, []
    main_targets = {}
    for q in questions:
        record = results.get(q.id, {})
        if q.question_type != 'structure' and 'table_target' in task_check_contract(q):
            target = ((record.get('selected_candidate') or {}).get('task_scope_audit') or {}).get('task_checks', {}).get('table_target')
            if (isinstance(target, dict) and all(type(target.get(k)) is int and target[k] > 0 for k in ('page', 'ordinal'))):
                key = q.file_name, normalize(q.table_hint)
                position = target['page'], target['ordinal']
                for other_id, old in main_targets.get(key, []):
                    if old != position:
                        conflicts.append({'ids': sorted({other_id, q.id}), 'anchor': list(key),
                                          'reason': '同一主表的标题/列数/左右表头选择了不同物理表'})
                main_targets.setdefault(key, []).append((q.id, position))
        # 继承结果没有新事实时明确缺席，不把无检查当作一致。
        for fact in record.get('facts', []):
            key = (q.file_name,) + anchor(fact)
            value = _numeric_key(fact.get('value'), fact.get('numeric_format', 'standard'))
            value = ('number', value) if value is not None else ('text', str(fact.get('value')).strip())
            for other_id, old in index.get(key, []):
                # 同题可把一个时间格的多个任务/文字片段逐项列出，不是两个题的取值冲突。
                if other_id != q.id and old != value:
                    conflicts.append({'ids': sorted({other_id, q.id}), 'anchor': list(key),
                                      'reason': '同一原文单元格的跨题声明不一致'})
            index.setdefault(key, []).append((q.id, value))
    unique = {str(c): c for c in conflicts}
    # 同牌号名义成分可暴露计数遗漏；仅简单Ti公式、全为主要量级且明确不含Ti时检查。
    nominal = {}
    for q in questions:
        answer = results.get(q.id, {}).get('answer')
        codes = re.findall(r'(?<![A-Za-z0-9])[A-Z]{1,4}\d+(?:-[A-Za-z0-9]+)?(?![A-Za-z0-9])', q.question)
        if '名义化学成分' not in q.question or len(codes) != 1 or not isinstance(answer, str):
            continue
        formula = re.fullmatch(r'Ti((?:-\d+(?:\.\d+)?[A-Z][a-z]?)+)', answer.strip())
        if formula:
            parts = re.findall(r'-(\d+(?:\.\d+)?)([A-Z][a-z]?)', formula[1])
            if parts and all(_numeric_key(n) >= 1 for n, _ in parts):
                nominal.setdefault((q.file_name, codes[0]), []).append((q.id, {symbol for _,symbol in parts}))
    for q in questions:
        if not re.search(r'主要元素.*(?:种类数|种数)', q.question) or not re.search(r'不含\s*Ti', q.question):
            continue
        codes = re.findall(r'(?<![A-Za-z0-9])[A-Z]{1,4}\d+(?:-[A-Za-z0-9]+)?(?![A-Za-z0-9])', q.question)
        if len(codes) != 1:
            continue
        number = _numeric_key(results.get(q.id, {}).get('answer'))
        for other, symbols in nominal.get((q.file_name, codes[0]), []):
            if number is not None and number != len(symbols):
                conflict = {'ids': sorted({other,q.id}), 'anchor':[q.file_name,codes[0],'nominal_elements'],
                            'reason':'主要元素数量与同牌号名义成分的元素集合不一致', 'declared_symbols':sorted(symbols)}
                unique[str(conflict)] = conflict
    for conflict in unique.values():
        for qid in conflict['ids']:
            record = results[qid]
            previous = record.get('consistency_conflicts', [])
            record['consistency_conflicts'] = previous + ([conflict] if conflict not in previous else [])
            record['warnings'] = list(dict.fromkeys(record.get('warnings', []) + [conflict['reason']]))
            record['status'] = 'uncertain'
            record['validation_level'] = 'consistency_conflict'
    return list(unique.values())
