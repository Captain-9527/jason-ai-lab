"""运算和定位的语义绑定；只报矛盾或证据缺口，不猜替代答案。"""
import re

from ..schemas import PipelineError


def compact(text):
    return re.sub(r'[\s：:，,。；;“”"\'（）()]', '', str(text)).casefold()


def subtraction_terms(text):
    match = re.search(r'(?:请)?计算\s*(.+?)\s*(?:减去|减(?!值|少|免|记))\s*(.+?)[。？?]?$', text)
    return [v.rstrip('。？?') for v in match.groups()] if match else None


def validate_operation_binding(q, plan):
    """绑定由records生成；旧协议缺绑定只标风险，显式反向则拒绝。"""
    if plan.get('op') not in {'difference', 'absolute_difference'}:
        return []
    warnings = []
    explicit = subtraction_terms(q.question)
    if explicit and plan['op'] != 'difference':
        raise PipelineError('题干明确相减，不能擅自取绝对差')
    ambiguous = bool(re.search(r'差值|相差|差额', q.question))
    meaning = plan.get('difference_mode')
    if meaning is not None and meaning not in {'signed', 'absolute'}:
        raise PipelineError('difference_mode须为signed/absolute')
    if ambiguous and meaning is None:
        warnings.append('差值未声明有向差或绝对差；复算通过不能消除题意歧义')
    if meaning and (meaning == 'signed') != (plan['op'] == 'difference'):
        raise PipelineError('差值口径与运算操作不一致')
    operands = plan.get('operand_bindings')
    if not isinstance(operands, list) or len(operands) != 2:
        return warnings + ['减法缺少操作数与原行列字段绑定，尚未验证题干顺序']
    if any(not isinstance(v, dict) or any(not isinstance(v.get(k), str) or not v[k].strip()
           for k in ('row', 'column', 'value')) for v in operands):
        raise PipelineError('operand_bindings须含两项原文row/column/value')
    if [v['value'] for v in operands] != plan.get('values'):
        raise PipelineError('操作数字段绑定与实际values不一致')
    if explicit:
        common_parts = ({compact(operands[0][k]) for k in ('row', 'column')}
                        & {compact(operands[1][k]) for k in ('row', 'column')})
        def matches(term, record):
            label = compact(record['row'] + record['column'])
            term = compact(term)
            years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', term)
            if any(year not in label for year in years):
                return False
            parts = [compact(record[k]) for k in ('row','column')
                     if compact(record[k]) not in common_parts]
            distinctive = [v for v in parts if len(v) >= 2 and not v.isdecimal()]
            return bool(term and (term in label or any(v in term for v in distinctive)
                                  or (years and not distinctive)))
        aligned = all(matches(t, v) for t, v in zip(explicit, operands))
        reversed_order = all(matches(t, v) for t, v in zip(explicit, reversed(operands)))
        if reversed_order and not aligned:
            raise PipelineError('操作数顺序与题干相减字段反向，不能只验证算术')
        if not aligned:
            warnings.append('相减字段与题干未唯一绑定，需核对行列及期间')
    return warnings


def lookup_target_contract(q):
    return (q.route == 'lookup' and q.question_type != 'structure'
            and bool(re.search(r'提取[“"].+?[”"]', q.question))
            # 分组叶子列已有column_group逐列绑定，不再要求第二份目标事实。
            and not re.search(r'\blevels\b|(?:因素|因子).*水平', q.question, re.I))


def audit_semantic_checks(q, raw):
    warnings = []
    checks = raw.get('task_checks') or {}
    if not isinstance(checks, dict):
        raise PipelineError('task_checks须为对象')
    if lookup_target_contract(q):
        target = checks.get('lookup_target')
        if target is None:
            warnings.append('缺少所问目标与实际原文标签的匹配声明')
        elif (not isinstance(target, dict) or target.get('match_kind') not in {'exact', 'synonym', 'absent'}
              or any(not isinstance(target.get(k), str) for k in ('requested_label', 'matched_label', 'basis'))
              or not target['requested_label'].strip() or not target['basis'].strip()
              or compact(target['requested_label']) not in compact(q.question)):
            raise PipelineError('lookup_target须声明题内所问标签、实际标签、匹配类型及依据')
        elif target['match_kind'] == 'absent':
            if raw.get('answer') is not None:
                raise PipelineError('原图不存在所问目标，不能用相似姓名或邻行值替代')
        elif target['match_kind'] == 'exact' and compact(target['requested_label']) != compact(target['matched_label']):
            raise PipelineError('声明精确匹配但原文标签不同，禁止默改姓名或对象')
        elif target['match_kind'] == 'synonym':
            warnings.append('目标采用同义/近似标签，尚无精确匹配依据，不能自动视为已确认')
    if '第一名' in q.question:
        selection = checks.get('selection')
        if (not isinstance(selection, dict) or selection.get('mode') not in {'first_row', 'rank_1', 'argmax'}
                or not isinstance(selection.get('basis'), str) or not selection['basis'].strip()):
            warnings.append('第一名未声明首条/排名第一/最大值口径及原图依据')
    if re.search(r'Row\s*\d', q.question, re.I) and checks.get('coordinate_reference') not in {'worksheet_rows', 'printed_labels'}:
        warnings.append('Row区域未声明工作表行号或表内标签坐标参照')
    match = re.search(r'RowSpan\s*=\s*["“]?(\d+)["”]?.*?([两二三四五六七八九十\d]+)个', q.question, re.I)
    if match:
        count = checks.get('region_count')
        expected = int(match[2]) if match[2].isdigit() else {'两':2,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}.get(match[2])
        if not isinstance(count, list):
            warnings.append('缺少逐个实际合并区域，不能按题干预设个数直接乘跨度')
        else:
            coordinates = []
            for item in count:
                if (not isinstance(item, dict) or any(type(item.get(k)) is not int for k in ('row','col','rowspan','colspan'))
                        or min(item['row'],item['col']) < 0 or min(item['rowspan'],item['colspan']) < 1
                        or item['rowspan'] != int(match[1])):
                    raise PipelineError('合并区域缺合法逻辑位置/跨度，或不满足题干RowSpan条件')
                coordinates.append(tuple(item[k] for k in ('row','col','rowspan','colspan')))
            if len(set(coordinates)) != len(coordinates):
                raise PipelineError('合并区域重复，不能把同一格计成两个')
            if len(count) != expected:
                raise PipelineError('原图区域个数与题干前提冲突，不得补造合并块')
    plans = raw.get('verifications', raw.get('verification', []))
    if isinstance(plans, dict):
        plans = [plans]
    for plan in plans if isinstance(plans, list) else []:
        if isinstance(plan, dict):
            warnings += validate_operation_binding(q, plan)
    return list(dict.fromkeys(warnings))
