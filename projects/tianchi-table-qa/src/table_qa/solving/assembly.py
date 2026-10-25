"""单一事实协议：由原文记录组装答案，不修补矛盾的历史答案。"""
from copy import deepcopy
import re

from ..schemas import PipelineError
from ..validation.answer import answer_contract
from .calculator import calculate, numeric
from .header_facts import header_contract, assemble_header
from ..validation.task_scope import header_side
from ..validation.numeric_locale import declared_format
from ..validation.operation_scope import validate_operation_binding


def question_calculation_plan(q, plan):
    """仅按明确题意统一百分点单位；不得偷偷降低计算精度。"""
    plan = deepcopy(plan)
    if (re.search(r'百分点|percentage\s+points?', q.question, re.I)
            and plan.get('op') == 'difference' and plan.get('values')
            and all(str(v).strip().endswith(('%', '％')) for v in plan['values'])):
        if 'scale' in plan and numeric(plan['scale']) != 100:
            raise PipelineError('百分点差额与百分数操作数的scale冲突')
        plan['scale'] = '100'
    return plan


def validate_rounding(q, plan, answer):
    if plan.get('digits') is None or plan.get('op') not in {'sum', 'product', 'mean', 'difference', 'absolute_difference', 'ratio', 'growth_rate', 'min', 'max'}:
        return
    if re.search(r'保留\s*[零一二三四五六七八九十\d]+\s*位|精确到|四舍五入|取整|整数|decimal\s+places?|round(?:ed)?\s+to', q.question, re.I):
        return
    unrounded = {k: v for k, v in plan.items() if k != 'digits'}
    exact = calculate(unrounded, unrounded['values'], unrounded.get('labels'))
    if numeric(exact) != numeric(answer):
        raise PipelineError('题目未要求舍入，模型digits改变了计算结果；不接受无依据的精度损失')


def facts_contract(q):
    if header_side(q):
        return 'header_facts', header_contract()
    common = {'evidence': [{'page': '整数页码', 'bbox_format': 'pixel（推荐）/norm999/norm1000',
                            'image_size': '当前输入视图[width,height]整数',
                            'bbox': '按本条bbox_format的4个数；pixel直接写当前上传图像素'}],
              'grounding': {'table': '表名', 'rows': '范围字符串', 'columns': '列路径字符串', 'scope': '范围字符串'}}
    if q.question_type == 'structure':
        if (re.search(r'heatmap|热力图', q.question + ' ' + q.table_hint, re.I)
                and re.search(r'表头|\bheaders?\b', q.question, re.I)):
            return 'matrix_facts', {**common, 'matrix_axes': {
                'page': '整数页码', 'column_axis_title': '横轴标题字符串；不属于类别名',
                'row_axis_title': '纵轴标题字符串；无则空字符串',
                'column_labels': ['每列类别原名，不含轴标题'], 'row_labels': ['每行指标原名，完整有序']}}
        return 'table_facts', {**common,
            'table_extent': {'page': '整数', 'col_count': '含行标签列/空白角格的完整列数整数',
                             'header_rows': '逻辑表头行数整数', 'data_row_labels': ['数据区每个叶子逻辑行的原标签，完整有序；嵌套表须逐个子行列出，不能只列外层分组名代替全部子行'],
                             'header_ranges': '表头分散时必填[[start,end],...]，0起半开行区间，长度之和等于header_rows',
                             'header_regions': '仅嵌套/局部列表头必填[[row_start,row_end,col_start,col_end],...]，0起半开矩形，覆盖全部真实表头，不含旁侧数据格',
                             'nested_groups': '嵌套表必填[{label:外层组原名,header_range:[起行,止行],body_range:[起行,止行],columns:[起列,止列]}]；穷举每个外层组，0起半开范围',
                             'note_rows': '边框内注释行数整数，表外说明不算'},
            'cells': [{'text': '原文或空字符串', 'row': '0起整数', 'col': '0起整数',
                       'rowspan': '正整数', 'colspan': '正整数'}]}
    record = {'value': '原文值字符串', 'row': '原行名字符串', 'column': '原列名字符串', 'level': '主类/子类/合计',
              'numeric_format': '仅非标准数值时声明decimal_comma/dot_grouping，否则省略或standard',
              'numeric_format_basis': '非standard时必填原图语言/同列格式/单位依据'}
    common['scope_check'] = {'complete': '布尔值', 'excluded': ['排除的表头/合计/其他范围']}
    if q.route == 'enumeration':
        return 'enumeration_facts', {**common, 'records': [record]}
    if q.route in {'calculation', 'filter'}:
        return 'calculation_facts', {**common, 'records': [{**record, 'label': '结果需要名称时的原名'}],
            'operations': [{'op': '受控操作；名称极值用argmax/argmin',
                            'indices': '可选记录索引数组；缺省使用全部记录',
                            'predicate': {'operator': '仅需条件筛选时填写gt/ge/lt/le/eq/ne',
                                          'value': '与原values相同数值尺度的阈值字符串；裸26.67与带%数值不可混淆',
                                          'missing': '有缺测值时明确exclude，否则error',
                                          'range_policy': '有区间值时必须明确all/any/lower/upper；all整个区间满足，any存在满足值，不能默默取中点'},
                            'difference_mode': '差值时声明signed/absolute，分别使用difference/absolute_difference',
                            'digits': '仅题目明确要求小数位时提供0到12整数，否则省略',
                            'separator': '仅string多名称结果使用，如逗号空格'}]}
    return None, None


def assemble_response(q, raw, mode, layout_hints=None, grid_hints=None):
    """返回独立标准候选与来源模式；旧协议只校验，不擅自改其尺寸或答案。"""
    fact_key = {'table_facts': 'table_extent', 'matrix_facts': 'matrix_axes', 'enumeration_facts': 'records',
                'calculation_facts': 'records', 'header_facts':'header_grid'}[mode]
    wrapper = None
    if mode in raw:
        inner = raw[mode]
        modes = {'table_facts', 'matrix_facts', 'enumeration_facts', 'calculation_facts', 'header_facts'}
        if (not isinstance(inner, dict) or any(k in raw for k in inner)
                or fact_key not in inner or modes.intersection(inner)
                or any(k in raw for k in modes - {mode})
                or any(k in raw for k in ('answer', 'full_table_check', 'verification', 'verifications'))):
            raise PipelineError('事实包装层与顶层冲突，不能猜测字段优先级')
        wrapper = mode
        raw = {**{k: v for k, v in raw.items() if k != mode}, **inner}
    if fact_key not in raw:
        if mode == 'header_facts':
            raise PipelineError('表头事实协议须返回完整header_grid，不接受旧answer绕过网格')
        return raw, 'legacy_response'
    if any(k in raw for k in ('answer', 'full_table_check', 'verification', 'verifications')):
        raise PipelineError('事实协议禁止同时返回answer/full_table_check/verification；仅提供契约中的原文事实')
    value = deepcopy(raw)
    if wrapper:
        value['fact_wrapper_removed'] = wrapper
    if mode == 'header_facts':
        if 'task_checks' in raw or 'interpretation' in raw:
            raise PipelineError('表头事实不重复声明task_checks/interpretation，扫描由程序生成')
        return assemble_header(q,value), 'program_assembled'
    if mode == 'matrix_facts':
        axes = value['matrix_axes']
        if (not isinstance(axes, dict) or type(axes.get('page')) is not int
                or any(not isinstance(axes.get(k), str) for k in ('column_axis_title', 'row_axis_title'))
                or any(not isinstance(axes.get(k), list) or not axes[k]
                       or any(not isinstance(v, str) or not v.strip() for v in axes[k])
                       for k in ('column_labels', 'row_labels'))):
            raise PipelineError('matrix_axes必须包含页码、独立轴标题和完整行列标签数组')
        title = axes['column_axis_title'].strip()
        if title and any(re.match(re.escape(title)+r'\s+\S', label, re.I) for label in axes['column_labels']):
            raise PipelineError('列类别中混入独立横轴标题，请重新读图分离，程序不自动删除文字')
        cells = [{'text': text, 'row': 0, 'col': c, 'rowspan': 1, 'colspan': 1}
                 for c, text in enumerate([''] + axes['column_labels'])]
        if not re.search(r'表头|\bheaders?\b', q.question, re.I):
            raise PipelineError('matrix_facts目前仅支持表头恢复，整表数值恢复须使用table_facts')
        converted = {**value, 'table_extent': {'page': axes['page'], 'col_count': len(axes['column_labels'])+1,
                     'header_rows': 1, 'data_row_labels': axes['row_labels'], 'note_rows': 0}, 'cells': cells}
        return assemble_response(q, converted, 'table_facts')
    if mode == 'table_facts':
        extent = value['table_extent']
        if not isinstance(extent, dict):
            raise PipelineError('table_extent必须是对象')
        source = extent.get('body_row_source', 'model')
        if source == 'pdf_vector_grid':
            if 'cells' in extent:
                if 'cells' in value:
                    raise PipelineError('矢量表头cells重复出现在顶层与table_extent，不能猜测优先级')
                value['cells'] = extent.pop('cells')
                value['grid_cells_unwrapped'] = True
            grids = [g for g in (grid_hints or []) if g['grid_id'] == extent.get('grid_id')
                     and g['page'] == extent.get('page')]
            if len(grids) != 1 or set(extent) != {'page', 'grid_id', 'body_row_source'}:
                raise PipelineError('矢量网格引用必须唯一且不得重复声明尺寸或数据行')
            grid = grids[0]; texts = value.get('cells')
            expected = {c['grid_cell_id']: c for c in grid['header_cells']}
            expansions = []
            if isinstance(texts, list) and all(isinstance(c, dict) and set(c) == {'grid_cell_id','text'}
                                              and isinstance(c['text'],str) for c in texts):
                present = {c['grid_cell_id'] for c in texts}
                for cell in list(texts):
                    parent = expected.get(cell['grid_cell_id'])
                    lines = cell['text'].splitlines()
                    if not parent or parent['colspan'] < 2 or parent['rowspan'] != 1 or len(lines) != 2:
                        continue
                    children = sorted([c for key,c in expected.items() if key not in present
                        and c['row'] == parent['row']+1 and parent['col'] <= c['col']
                        and c['col']+c['colspan'] <= parent['col']+parent['colspan']], key=lambda c:c['col'])
                    labels = lines[1].split()
                    if (len(children) < 2 or len(children) != len(labels) or not lines[0].strip()
                            or sum(c['colspan'] for c in children) != parent['colspan']):
                        continue
                    expansions.append({'parent':cell['grid_cell_id'],'original_text':cell['text'],
                                       'children':[c['grid_cell_id'] for c in children]})
                    cell['text'] = lines[0].strip()
                    texts.extend({'grid_cell_id':c['grid_cell_id'],'text':label} for c,label in zip(children,labels))
                    present.update(c['grid_cell_id'] for c in children)
            if (not isinstance(texts, list) or len(texts) != len(expected)
                    or any(not isinstance(c, dict) or set(c) != {'grid_cell_id', 'text'}
                           or not isinstance(c['text'], str) or c['grid_cell_id'] not in expected for c in texts)
                    or len({c['grid_cell_id'] for c in texts}) != len(expected)):
                raise PipelineError('须按完整矢量表头格ID逐格填写原文，不能漏格、重复或改写几何')
            answer = {'row_count':grid['row_count'],'col_count':grid['col_count'],
                      'cells':[{**{k:v for k,v in expected[c['grid_cell_id']].items() if k != 'grid_cell_id'},
                                'text':c['text']} for c in texts]}
            value['answer'] = answer
            value['pdf_grid_check'] = {k:grid[k] for k in ('grid_id','page','row_count','col_count','header_rows','source')}
            value['pdf_grid_check']['text_expansions'] = expansions
            value['pdf_grid_check']['cells_unwrapped'] = value.get('grid_cells_unwrapped', False)
            value['full_table_check'] = {'scope':'full_table','page':grid['page'],
                'row_count':grid['row_count'],'col_count':grid['col_count'],
                'row_breakdown':{'header':grid['header_rows'],
                                 'body':grid['row_count']-grid['header_rows']-grid['note_rows'],
                                 'notes':grid['note_rows']},
                'body_row_source':'pdf_vector_grid','grid_id':grid['grid_id'],
                'basis':'已转正PDF完整矢量网格；首两列相同跨行高度定义表头，末尾整宽行独立计数；文字仍须读图核对'}
            return value, 'program_assembled'
        if source == 'pdf_layout_rows':
            hints = [h for h in (layout_hints or []) if h.get('page') == extent.get('page')]
            rows = hints[0].get('body_rows') if len(hints) == 1 else None
            if (extent.get('data_row_labels') is not None or not isinstance(rows, list) or not rows
                    or any(not isinstance(row, str) or not row.strip() for row in rows)):
                raise PipelineError('显式引用PDF数据行须有唯一同页输入body_rows，且不得同时提供data_row_labels')
            extent['data_row_labels'] = list(rows)
        elif source != 'model':
            raise PipelineError('未知body_row_source；仅允许model或pdf_layout_rows')
        labels = extent.get('data_row_labels')
        if (not isinstance(labels, list) or any(not isinstance(v, str) or not v.strip() for v in labels)
                or any(type(extent.get(k)) is not int or extent[k] < 0 for k in ('header_rows', 'note_rows'))
                or type(extent.get('col_count')) is not int or extent['col_count'] < 1
                or type(extent.get('page')) is not int):
            raise PipelineError('整表事实缺少合法列数、表头/注释行数、页码或完整数据行标签数组')
        counts = {'header': extent['header_rows'], 'body': len(labels), 'notes': extent['note_rows']}
        if not labels and extent.get('empty_body') is not True:
            raise PipelineError('整表数据行标签为空：局部表头输出仍须列全数据行；真实空表才显式empty_body=true')
        value['answer'] = {'row_count': sum(counts.values()), 'col_count': extent['col_count'], 'cells': value.get('cells')}
        value['full_table_check'] = {'scope': 'full_table', 'row_count': sum(counts.values()),
            'col_count': extent['col_count'], 'page': extent['page'], 'row_breakdown': counts,
            'basis': '程序按表头/完整数据行标签/表内注释组装；行标签仍须原图核验',
            'body_range': {'first': labels[0] if labels else '', 'last': labels[-1] if labels else ''},
            'body_row_labels': labels, 'empty_body': extent.get('empty_body') is True and not labels}
        value['full_table_check']['body_row_source'] = source
        if extent.get('header_ranges') is not None:
            value['full_table_check']['header_ranges'] = extent['header_ranges']
        if extent.get('header_regions') is not None:
            value['full_table_check']['header_regions'] = extent['header_regions']
        if extent.get('nested_groups') is not None:
            value['full_table_check']['nested_groups'] = extent['nested_groups']
    else:
        records = value['records']
        if not isinstance(records, list) or any(not isinstance(r, dict) or any(
                not isinstance(r.get(k), str) or not r[k].strip() for k in ('value', 'row', 'column', 'level')) for r in records):
            raise PipelineError('records必须是含value/row/column/level原文字符串的记录数组')
        if mode == 'enumeration_facts':
            names_requested = (bool(re.search(r'分类|类别|名称|种类|品名', q.question))
                               and not re.search(r'编号|编码|代号|年份|日期|数值|金额|比例|计提率|百分比', q.question))
            if names_requested and records and all(re.fullmatch(r'[+-]?\d+(?:\.\d+)?[%％]?', r['value'].strip()) for r in records):
                raise PipelineError('题目要求类别/名称，records却全部为数值；请重新定位分类列，不能用相邻比例列代替')
            requested_totals = (bool(re.search(r'合计|总计|小计|汇总', q.question))
                                and not re.search(r'不含|不包括|排除|去掉|除去|不计入', q.question))
            aggregate_levels = {'合计', '总计', '小计', '汇总', 'total', 'subtotal', 'aggregate'}
            excluded = [r for r in records if not requested_totals and r['level'].strip().casefold() in aggregate_levels]
            selected = [r for r in records if r not in excluded]
            value['answer'] = [r['value'] for r in selected]
            value['items'] = [{k: r[k] for k in ('value', 'row', 'column', 'level')} for r in selected]
            value['program_excluded_records'] = excluded
            if excluded and isinstance(value.get('scope_check'), dict) and isinstance(value['scope_check'].get('excluded'), list):
                value['scope_check']['excluded'] += ['程序按分类枚举规则排除合计行：'+r['row'] for r in excluded]
        else:
            operations = value.get('operations')
            if not isinstance(operations, list) or not operations or any(not isinstance(p, dict) for p in operations):
                raise PipelineError('operations必须为非空运算对象数组')
            plans, answers = [], []
            for operation in operations:
                ids = operation.get('indices', list(range(len(records))))
                if (not isinstance(ids, list) or any(type(i) is not int or not 0 <= i < len(records) for i in ids)
                        or len(set(ids)) != len(ids)):
                    raise PipelineError('运算记录索引越界或重复')
                selected = [records[i] for i in ids]
                plan = {k: v for k, v in operation.items() if k != 'indices'}
                if plan.get('predicate', 'absent') is None:
                    plan.pop('predicate')  # 可选条件null等价于未提供，不放宽实际条件校验。
                plan['values'] = [r['value'] for r in selected]
                if 'operand_bindings' in operation or 'numeric_formats' in operation:
                    raise PipelineError('操作数绑定与数字格式由records生成，不在operations重复声明')
                formats = [declared_format(r) for r in selected]
                if any(style != 'standard' for style in formats):
                    plan['numeric_formats'] = formats
                if plan.get('op') in {'difference', 'absolute_difference'}:
                    plan['operand_bindings'] = [{k: r[k] for k in ('row','column','value')} for r in selected]
                    problems = validate_operation_binding(q, plan)
                    if problems:
                        value.setdefault('semantic_warnings', []).extend(problems)
                if plan.get('op') in {'argmax', 'argmin', 'select'}:
                    plan['labels'] = [r.get('label', r['row']) for r in selected]
                plan = question_calculation_plan(q, plan)
                answer = calculate(plan, plan['values'], plan.get('labels'))
                validate_rounding(q, plan, answer)
                if q.route == 'filter' and plan['op'] in {'argmax', 'argmin'} and len(selected) != len(records):
                    if len({r['column'] for r in records}) != 1:
                        raise PipelineError('极值候选跨多个列且只选择部分indices，无法确认完整比较范围')
                    full = {**plan, 'values': [r['value'] for r in records],
                            'labels': [r.get('label', r['row']) for r in records]}
                    if 'numeric_formats' in full:
                        full['numeric_formats'] = [declared_format(r) for r in records]
                    if calculate(full, full['values'], full['labels']) != answer:
                        raise PipelineError('极值只比较部分indices，结果与完整候选范围不一致，可能漏掉更大值或并列项')
                    value['extreme_domain_check'] = {'compared_record_count': len(records), 'status': 'all_records_agree'}
                if answer_contract(q) == 'string' and isinstance(answer, list):
                    raise PipelineError('多名称string答案需在operation中提供separator，不能只取首项')
                plans.append(plan)
                answers.append(answer)
            value['answer'] = answers[0] if len(answers) == 1 else answers
            if answer_contract(q) == 'json_array' and not isinstance(value['answer'], list):
                value['answer'] = [value['answer']]
            value['verifications'] = plans
    return value, 'program_assembled'
