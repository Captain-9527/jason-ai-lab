"""题意范围与原文保真检查；只标歧义或拒绝非法候选，不猜替代答案。"""
import re
from copy import deepcopy

from ..schemas import PipelineError
from ..solving.calculator import calculate, numeric
from .answer import answer_contract, normalize_array_nulls
from .numeric_locale import declared_format, normalize_numeric_text
from .operation_scope import lookup_target_contract, audit_semantic_checks


def header_side(q):
    """仅标量表头位置题适用；结构恢复、字段列表不套用方向扫描。"""
    if q.question_type == 'structure' or answer_contract(q) != 'string':
        return None
    match = re.search(r'最([左右]).*(?:表头|字段名)', q.question)
    return {'左': 'left', '右': 'right'}[match[1]] if match else None


def grouped_levels_question(q):
    """只约束原文水平值查找，不把范围或水平数量计算改成多列提取。"""
    return (q.question_type == 'extract' and q.route == 'lookup'
            and not re.search(r'\brange\b|范围|多少|数量|个数|最高|最低|最大|最小', q.question, re.I)
            and bool(re.search(r'\blevels\b|(?:因素|因子).*水平', q.question, re.I)))


def apply_answer_conventions(q, raw, policy):
    """在副本应用用户确认口径，原模型响应不变且保留改写审计。"""
    value = deepcopy(raw)
    changes = []
    if answer_contract(q) == 'number' and value.get('numeric_format') is not None:
        style = declared_format(value)
        if style != 'standard' and policy.get('numeric_locale', 'explicit') == 'explicit':
            old = value.get('answer')
            value['answer'] = normalize_numeric_text(old, style)
            if value['answer'] != old:
                changes.append({'rule': 'explicit_numeric_locale', 'numeric_format': style,
                                'basis': value['numeric_format_basis'], 'before': old, 'after': value['answer']})
    if answer_contract(q) == 'json_array':
        old = value.get('answer')
        new = normalize_array_nulls(old, policy)
        if new != old:
            value['answer'] = new
            for item, previous, current in zip(value.get('items') or [], old, new):
                if isinstance(item, dict) and item.get('value') == previous:
                    item['value'] = current
            changes.append({'rule': 'array_nulls', 'before': old, 'after': new})
    interpretation = value.get('interpretation')
    if (answer_contract(q) == 'string' and re.search(r'最左.*表头', q.question) and isinstance(interpretation, dict)
            and interpretation.get('blank_corner') is True):
        right = interpretation.get('nearest_right')
        if (isinstance(right, dict) and right.get('same_row') is True
                and isinstance(right.get('text'), str) and right['text'].strip()):
            old, new = value.get('answer'), right['text']
            if old not in (None, '', '空白', '未命名', new):
                raise PipelineError('空白左表头答案与同一表头行右侧最近非空原文不一致，不能取下方行标签')
            value['answer'] = new
            if old != new:
                changes.append({'rule': 'blank_left_header_nearest_right', 'before': old, 'after': new})
    if changes:
        value['answer_normalization'] = changes
    return value


def task_requirements(q, cfg):
    rules = {'literal': '提取文字逐字保留；邮箱不插入空格，数组原文保留小数逗号、尾零和符号，不擅自标准化。'}
    rules['numeric_locale'] = ('number原值若明确采用小数逗号/点分隔千位，返回原文answer及numeric_format=decimal_comma/dot_grouping，'
                               'numeric_format_basis说明语言、同列格式或单位依据，由程序转换数值；普通三位小数不能当千位。数组字符串仍保留原文。')
    rules['operation_binding'] = ('减法逐项保留操作数的原行/完整列名/期间和值；按题干减号顺序提交，不擅自取绝对值。'
                                  '差值语义声明difference_mode=signed/absolute，对应difference/absolute_difference；存在歧义如实保留。')
    if cfg.get('answer', {}).get('array_nulls') == 'empty_string':
        rules['array_missing'] = '数组内空白/缺值用空字符串，不写null或字符串null；整题无法识别仍answer=null，不当作已完成。'
    if q.question_type == 'structure':
        rules['table_selection'] = {
            'policy': cfg['run'].get('structure_target_policy', 'as_requested'),
            'rule': '先按标题、业务范围和父列路径选表。first表示首个匹配题意的物理表，不是选最后一张同主题变动表；不合并无关子表。'}
    if q.question_type != 'structure' and '主表' in q.question:
        rules['main_table'] = ('先定位同一张目标物理表；主表标题、列数及左右字段不能分别取自不同子表。'
                              'hint仅参考，题意优先；没有显式表头时不为寻找有字的表头切换到另一张表。'
                              'task_checks.table_target用页码和本页自上而下、自左向右的物理表序号标识目标，序号从1起。')
    if grouped_levels_question(q):
        rules['grouped_columns'] = ('区分分组列Levels/水平下的Range/范围与离散水平子列；未明确问Range时读取离散水平列，'
                                    '不要只取首个Range列。按原表列顺序逐项声明列路径和值；同一表的原图与重绘副本不重复计入。')
    if q.route == 'enumeration':
        rules['hierarchy'] = '未限定仅主类时，逐行保留主类和其中子类；同值不去重，合计另外排除，期初/期末使用同一层级口径。'
    if q.route == 'field_list':
        match = re.search(r'答案[:：](.+)', q.question)
        if match:
            rules['requested_fields'] = [v.strip(' 。？?') for v in match[1].split('、')]
    if re.search(r'最[左右].*表头|列数|多少列|几列', q.question):
        rules['header_scope'] = ('区分真实列头、左侧行标签与空白角格。最左侧角格为空时，在同一表头行向右取最近非空单元格原文，不取下方数据行或行标签；'
                                 '列数按目标数据表完整逻辑字段计，不把页面左右排版栏数当表格列数。'
                                 '无名序号列/空白角列也占一列，多级父表头不额外算一列。'
                                 '仅最左侧空白表头填interpretation.blank_corner=true、nearest_right={text:同一表头行右侧最近非空原文,same_row:true}，answer使用该text，证据指向该右邻单元格。')
    if side := header_side(q):
        rules['header_selection'] = (
            '先确定题目对应的目标物理表，从表顶向下定位首个真实表头行，再在同一行从'
            + ('左向右' if side == 'left' else '右向左')
            + '取第一个非空表头单元格原文。多级表头中有多个字段/分组名称的上行本身就是有效表头，'
              '不能跳到下面的叶子行；仅跨整表的标题/分区名可跳过，不机械把最上方所有文字当表头。'
              '合并单元格只列一次。不把表外标题、跨整表的分区标题、品牌/媒体标识、数据值或设备单位当字段名；'
              '含字段、具体值和单位的混合行，须逐格声明role，不能把数量值和计量单位冒充字段。'
              '同图多表先按题意/hint定位，不因另一张表有字就切换；hint冲突时题意优先。'
              'task_checks.header_scan按物理顺序列出完整选定表头行（含空格），声明表位置、行号和排除项；'
              '若跳过前行，preceding_rows逐行给出非表头类型；不得把有效分组行说成分区标题。'
              '只写简短定位事实，不输出推理或旧答案。')
    checks = task_check_contract(q)
    if checks:
        rules['task_checks'] = ('按response_contract.task_checks提供简短最终事实，不输出推理。字段存在与值可读分开判断；'
                               '字段列表允许一个字段多个值，answer_index指向实际输出位置，不按字段数强行截断。'
                               '极值逐项列出目标列全部可见候选（含附加项目），未明确限制不自行排除；'
                               '表格总行数逐行计数，不采用工作表边栏最大行号。')
    if re.search(r'最上|位于上方|最下|位于下方', q.question):
        rules['spatial_selection'] = '先按题干颜色/区域筛选，再按上下位置选格；比较单元格位置，不比较格内编号大小。'
    return rules


def task_check_contract(q):
    """只为适用题型附加同次事实字段；不调用模型，不按题号选择规则。"""
    if q.question_type == 'structure':
        return {}
    match = re.search(r'答案[:：](.+)', q.question)
    fields = [v.strip(' 。？?') for v in match[1].split('、')] if match else [q.question]
    presence = [v for v in fields if re.search(r'是否(?:含|包含|存在|有).*(?:列|字段)', v)]
    extrema = {v: 'min' if re.search(r'最低|最小', v) else 'max' for v in fields
               if match and re.search(r'最低|最高|最小|最大', v)
               and not re.search(r'科目|名称|学校|公司|类别|生产商|哪', v)}
    checks = {}
    if '主表' in q.question or header_side(q) or re.search(r'列数|多少列|几列', q.question):
        checks['table_target'] = {'page': '目标页整数', 'ordinal': '本页物理表阅读顺序，从1起整数',
                                  'name': '目标表原文标题或简短位置描述', 'basis': '简短定位事实；不输出推理'}
    if side := header_side(q):
        checks['header_scan'] = {
            'side': side, 'header_row': '目标表内0起的选定真实表头行号，整数',
            'cells': [{'col': '逻辑列号，0起整数，按从左到右严格递增',
                       'text': '同一表头行的单元格原文；空白写空字符串；合并格仅列一次',
                       'role': 'header/blank/title/section_title/branding/data/unit'}],
            'preceding_rows': [{'row': '在header_row之前的表内行号，0起整数，连续完整；header_row=0时空数组',
                                'kind': 'title/section_title/branding/data/unit/blank',
                                'text': '该行可见原文，空行为空字符串'}],
            'complete': '已覆盖这一整行而非只列选中一格，布尔值',
            'excluded': [{'text': '排除的原文；没有则整个excluded为空数组',
                          'kind': 'title/section_title/branding/data/unit/other'}]}
    if grouped_levels_question(q):
        checks['column_group'] = {'columns': ['实际选中各叶子列的完整父列路径，按原顺序'],
                                   'values': ['与columns逐项对应的原文值字符串'],
                                   'excluded': ['未采用的Range等列路径及理由'], 'scope_complete': '布尔值'}
    if presence:
        checks['presence'] = [{
            'field': field, 'answer_index': 'answer数组0起位置；标量为0',
            'label': '实际可见列名；不存在时空字符串', 'present': '布尔值', 'value_readable': '布尔值'} for field in presence]
    if extrema:
        checks['field_extrema'] = [{
            'field': field, 'answer_index': 'answer数组0起位置', 'op': op,
            'values': ['目标列全部可见数值原文'], 'scope_complete': '布尔值', 'excluded': ['排除项及理由；无则空数组']} for field, op in extrema.items()]
    if not match and re.search(r'(?:总行数|多少行|几行)', q.question):
        checks['table_rows'] = {'basis': 'table_rows',
                                'row_labels': ['按题意逐逻辑行列全；总行数含表头，仅数据行则不含表头；无行名用位置标识，不用侧边行号推算']}
    if lookup_target_contract(q):
        checks['lookup_target'] = {'requested_label': '题干所问目标原标签', 'matched_label': '原图实际匹配标签',
            'match_kind': 'exact/synonym/absent', 'basis': '原图匹配事实；近似姓名不视为精确匹配'}
    if '第一名' in q.question:
        checks['selection'] = {'mode': 'first_row/rank_1/argmax', 'basis': '首条、排名列或全范围极值的原图依据'}
    if re.search(r'Row\s*\d', q.question, re.I):
        checks['coordinate_reference'] = 'worksheet_rows/printed_labels；明确采用Excel边栏行号还是表内Row标签'
    if re.search(r'RowSpan.*[两二三四五六七八九十\d]+个', q.question, re.I):
        checks['region_count'] = [{'row': '0起逻辑行', 'col': '0起逻辑列', 'rowspan': '实际跨度', 'colspan': '实际跨度'}]
    return checks


def audit_task_checks(q, raw):
    """核对模型声明与答案的矛盾；声明缺失标风险，不从另一格补答案。"""
    contract = task_check_contract(q)
    if not contract:
        return [], {}
    checks = raw.get('task_checks')
    if checks is None:
        return ['缺少适用事实检查（字段存在性/极值/逻辑行数/表头扫描），未验证题意范围'], {}
    if not isinstance(checks, dict):
        raise PipelineError('task_checks须为对象')
    warnings = []
    answer = raw.get('answer')
    values = answer if isinstance(answer, list) else [answer]
    if 'table_target' in contract:
        target = checks.get('table_target')
        if target is None:
            warnings.append('缺少主表物理位置声明，未验证跨题选表一致性')
        elif (not isinstance(target, dict) or any(type(target.get(k)) is not int or target[k] < 1 for k in ('page', 'ordinal'))
              or any(not isinstance(target.get(k), str) or not target[k].strip() for k in ('name', 'basis'))):
            raise PipelineError('主表声明须有正整数页码/阅读顺序及非空名称/定位依据')
    if 'header_scan' in contract:
        scan = checks.get('header_scan')
        if scan is None:
            warnings.append('缺少表头方向扫描声明，未验证指定一侧首个非空字段')
        elif (not isinstance(scan, dict) or scan.get('side') != header_side(q)
              or type(scan.get('header_row')) is not int or scan['header_row'] < 0):
            raise PipelineError('表头扫描方向或表头行号与题意不一致')
        else:
            cells = scan.get('cells')
            if not isinstance(cells, list) or not cells:
                raise PipelineError('表头扫描须提供同一表头行的单元格数组')
            previous = -1
            for cell in cells:
                if (not isinstance(cell, dict) or type(cell.get('col')) is not int
                        or cell['col'] <= previous or not isinstance(cell.get('text'), str)):
                    raise PipelineError('表头扫描列号须非负且严格递增，原文须为字符串')
                previous = cell['col']
            roles = {'header','blank','title','section_title','branding','data','unit'}
            for cell in cells:
                if cell.get('role') not in roles or (cell['text'].strip() and cell['role']=='blank'):
                    raise PipelineError('表头扫描每格须声明合法角色，非空内容不能伪装空白')
            for index, cell in enumerate(cells[2:], 2):
                # 只拦截“数量字段—具体数量—计数单位”的完整邻接关系，不全局禁用单位词。
                label, value = cells[index-2:index]
                if (cell['role']=='header' and re.fullmatch(r'each|pcs?\.?|pieces?|units?',cell['text'].strip(),re.I)
                        and label['role']=='header' and re.fullmatch(r'qty\.?|quantity|数量',label['text'].strip(),re.I)
                        and value['role']=='data' and re.fullmatch(r'\d+(?:\.\d+)?',value['text'].strip())):
                    raise PipelineError('数量字段及具体数量之后的计数单位不能冒充表头字段')
            preceding = scan.get('preceding_rows')
            if (not isinstance(preceding, list) or len(preceding) != scan['header_row']
                    or any(not isinstance(row, dict) or type(row.get('row')) is not int or row['row'] != index
                           or row.get('kind') not in roles-{'header'} or not isinstance(row.get('text'), str)
                           for index,row in enumerate(preceding))):
                raise PipelineError('选择下方表头行须逐行声明之前的非表头行，不能跳过有效表头')
            nonempty = [cell['text'] for cell in cells if cell['role']=='header' and cell['text'].strip()]
            if not nonempty:
                raise PipelineError('选定表头行没有非空字段，不能从数据行补答案')
            expected = nonempty[0] if scan['side'] == 'left' else nonempty[-1]
            compact = lambda text: re.sub(r'\s+', ' ', text).strip()
            if not isinstance(answer, str) or compact(answer) != compact(expected):
                raise PipelineError('答案不是指定一侧首个非空表头，不能混用其他表头行或非表头内容')
            excluded = scan.get('excluded')
            if (not isinstance(excluded, list) or any(not isinstance(v, dict)
                    or not isinstance(v.get('text'), str)
                    or v.get('kind') not in {'title','section_title','branding','data','unit','other'} for v in excluded)):
                raise PipelineError('表头排除项须声明原文和合法类型')
            if any(compact(v['text']) == compact(answer) and v['kind'] != 'other' for v in excluded):
                raise PipelineError('答案同时被声明为非表头排除项，定位口径矛盾')
            if scan.get('complete') is not True:
                warnings.append('表头扫描未确认覆盖完整选定行，不能证明边缘字段完整')
    if 'column_group' in contract:
        group = checks.get('column_group')
        if group is None:
            warnings.append('缺少分组叶子列和值，未验证Levels与Range范围')
        elif (not isinstance(group, dict) or not isinstance(group.get('columns'), list)
              or not isinstance(group.get('values'), list) or not group['columns']
              or len(group['columns']) != len(group['values'])
              or any(not isinstance(v, str) or not v.strip() for v in group['columns'] + group['values'])):
            raise PipelineError('分组列须提供等长非空列路径和值字符串数组')
        else:
            actual = answer if isinstance(answer, list) else re.split(r'\s*[,，;；、\n]\s*', str(answer).strip())
            if actual != group['values']:
                raise PipelineError('分组列答案与逐项声明的叶子列值不一致，不能只答范围列')
            if group.get('scope_complete') is not True or not isinstance(group.get('excluded'), list):
                warnings.append('分组列未确认完整叶子列及排除范围')
    for kind in ('presence', 'field_extrema'):
        if kind not in contract:
            continue
        entries = checks.get(kind)
        if not isinstance(entries, list) or not entries:
            warnings.append(f'缺少{kind}逐字段事实')
            continue
        expected = {v['field']: v.get('op') for v in contract[kind]}
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise PipelineError(f'{kind}每项须为对象')
            field, index = entry.get('field'), entry.get('answer_index')
            if (not isinstance(field, str) or field not in expected or field in seen
                    or type(index) is not int or not 0 <= index < len(values)):
                raise PipelineError(f'{kind}字段/输出位置不合法或重复')
            seen.add(field)
            actual = values[index]
            if kind == 'presence':
                present, readable = entry.get('present'), entry.get('value_readable')
                if type(present) is not bool or type(readable) is not bool:
                    raise PipelineError('字段存在与值可读须分别声明布尔值')
                if present and (not isinstance(entry.get('label'), str) or not entry['label'].strip()):
                    raise PipelineError('字段存在须给出可见列名')
                if (present and actual in ('否', '没有', '不包含', '不存在')
                        or not present and actual in ('是', '有', '包含', '存在')):
                    raise PipelineError('字段存在性与答案矛盾；值不可读不代表没有该列')
                if actual not in ('否', '没有', '不包含', '不存在', '是', '有', '包含', '存在'):
                    warnings.append('字段存在性答案未明确，保留不确定')
            else:
                if entry.get('op') != expected[field]:
                    raise PipelineError('字段极值操作与题意不一致')
                computed = calculate({'op': entry['op']}, entry.get('values'))
                if numeric(actual) != numeric(computed):
                    raise PipelineError('字段极值答案与全部已声明候选复算不一致')
                if entry.get('scope_complete') is not True or not isinstance(entry.get('excluded'), list):
                    warnings.append('字段极值未确认完整候选及排除范围')
                elif entry['excluded']:
                    warnings.append('字段极值排除了可见项目，须核对题干是否明确限制范围')
        if set(expected) != seen:
            warnings.append(f'{kind}遗漏待核对字段')
    if 'table_rows' in contract:
        rows = checks.get('table_rows')
        if not isinstance(rows, dict):
            warnings.append('缺少表格逐行计数事实')
        elif rows.get('basis') != 'table_rows':
            raise PipelineError('表格总行数不能使用工作表行号或页面行数')
        elif (not isinstance(rows.get('row_labels'), list) or not rows['row_labels']
              or any(not isinstance(v, str) or not v.strip() for v in rows['row_labels'])):
            raise PipelineError('表格总行数须提供逐行非空标识')
        elif numeric(answer) != len(rows['row_labels']):
            raise PipelineError('表格总行数与逐行标识数量不一致')
    return warnings, {'task_checks': deepcopy(checks)}


def audit_task_scope(q, raw):
    """保留模型声明和同一事实集的区间敏感性，不增加模型请求。"""
    answer = raw.get('answer')
    if (re.search(r'email|e-mail|邮箱|电子邮件', q.question, re.I)
            and isinstance(answer, str) and re.search(r'\S\s+@|@\s+\S', answer)):
        raise PipelineError('邮箱原文候选含@两侧内部空格；须重新读图，程序不自动删除字符')
    warnings, audit = audit_task_checks(q, raw)
    interpretation = raw.get('interpretation')
    if isinstance(interpretation, dict):
        audit['interpretation'] = interpretation
        if (answer_contract(q) == 'string' and re.search(r'最[左右].*表头', q.question)
                and interpretation.get('blank_corner') is True):
            right = interpretation.get('nearest_right') or {}
            if not (re.search(r'最左.*表头', q.question) and isinstance(right, dict)
                    and right.get('same_row') is True and right.get('text') == answer
                    and isinstance(answer, str) and answer.strip()):
                warnings.append('空白表头尚未提供同一表头行右侧最近非空原文，不能认定符合已确认口径')
        if (re.search(r'列数|多少列|几列', q.question)
                and interpretation.get('column_scope') == 'page_layout'):
            raise PipelineError('把页面排版栏数作为数据表列数，目标逻辑字段范围未确认')
    plans = raw.get('verifications') or ([raw['verification']] if isinstance(raw.get('verification'), dict) else [])
    sensitivities = []
    for plan in plans if isinstance(plans, list) else []:
        if (not isinstance(plan, dict) or plan.get('op') not in {'count', 'select'}
                or not isinstance(plan.get('predicate'), dict)):
            continue
        if not any(re.search(r'\d\s*(?:~|～|至|–|—|-)\s*\d', str(v)) for v in plan.get('values', [])):
            continue
        predicate = plan['predicate']
        if predicate.get('range_policy') not in {'all', 'any', 'lower', 'upper'}:
            continue  # 实际非法区间计划由计算器拒绝。
        values = {}
        for policy in ('all', 'any'):
            alternate = {**plan, 'predicate': {**predicate, 'range_policy': policy}}
            values[policy] = calculate(alternate, alternate['values'], alternate.get('labels'))
        if values['all'] != values['any']:
            explicit = bool(re.search(r'整个区间|全部|任意|至少.*部分|可能|上限|下限|最小含量|最大含量', q.question))
            sensitivities.append({'chosen': predicate['range_policy'], **values, 'explicit_in_question': explicit})
            if not explicit:
                warnings.append('区间筛选all/any结果不同，题干未明确区间口径；复算一致不能消除语义歧义')
    if sensitivities:
        audit['range_sensitivity'] = sensitivities
    warnings += audit_semantic_checks(q, raw)
    return list(dict.fromkeys(warnings)), audit
