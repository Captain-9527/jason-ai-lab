"""原图直答与定向修复；复核前必须进行类型和证据范围检查。"""
import re
from ..schemas import PipelineError
from ..validation.answer import answer_contract, canonical, validate_candidate_answer
from ..validation.coordinates import evidence_pixels, emit_warnings
from .calculator import calculate
from ..processing.views import transform_box
from ..processing.grid import simple_grid_hint
from ..validation.structure_scope import validate_structure_scope, structure_scope, requested_cell_ranges
from ..validation.pdf_localization import localize_pdf_value
from ..validation.pdf_header import align_pdf_header, simple_pdf_column_hint, align_pdf_header_rowspans
from ..validation.grounding import validate_full_row_label, validate_prior_array
from ..validation.pdf_names import pdf_row_name_hints, validate_pdf_name_labels
from ..validation.pdf_grouped import validate_grouped_pdf_array
from ..validation.field_semantics import validate_field_semantics
from .assembly import facts_contract, assemble_response
from .header_facts import header_detail_views
from ..validation.task_scope import task_requirements, task_check_contract, audit_task_scope, apply_answer_conventions


def verification_plans(raw):
    """兼容单个verification对象/数组别名，但绝不让嵌套数组进入计算器。"""
    if 'verification' in raw and 'verifications' in raw:
        raise PipelineError('verification与verifications不能同时提供；单个计划用对象，多个用对象数组')
    checks = raw.get('verifications', raw.get('verification'))
    if 'verifications' in raw and not isinstance(checks, list):
        raise PipelineError('verifications必须为非空对象数组；单个对象使用verification字段')
    if isinstance(checks, dict):
        checks = [checks]
    if not isinstance(checks, list) or not checks or any(not isinstance(p, dict) for p in checks):
        raise PipelineError('复算计划必须为对象或非空对象数组，不能是嵌套数组、字符串或null')
    return checks


def validate_evidence(evidence, pages, cfg=None, warnings=None):
    if not isinstance(evidence, list) or not evidence:
        raise PipelineError("缺少原图证据位置")
    lookup = {p["page"]: p for p in pages}
    result = []
    for e in evidence:
        if not isinstance(e, dict) or type(e.get("page")) is not int or e["page"] not in lookup:
            raise PipelineError("证据页码不存在")
        page = lookup[e["page"]]
        result.append({"page": e["page"], "bbox": evidence_pixels(e, page["width"], page["height"], cfg, warnings)})
    return result


def solve_direct(q, pages, model, fresh=False, repair=None, extra_images=None, cfg=None):
    payload = {"question": q.public(), "pages": [{k: p[k] for k in ("page", "width", "height")} for p in pages]}
    payload["question"]["answer_contract"] = answer_contract(q)
    if cfg and cfg['run'].get('single_pass', False):
        payload['single_pass'] = True
        payload['task_requirements'] = task_requirements(q, cfg)
        if q.question_type != 'structure' and re.search(r'是否(?:横向|纵向|含|包含|存在|有)|主表或主区域的标题', q.question):
            payload['visual_task'] = {'scope': 'whole_target_table_or_image',
                                      'criterion': q.question,
                                      'rule': '图像属性/字段存在性判定，不是寻找同名字面字段后抄其邻值'}
        if q.question_type != 'structure':
            payload['response_contract'] = {
                'answer': answer_contract(q),
                'evidence': [{'page': '输入页码整数', 'bbox_format': 'pixel（推荐）/norm999/norm1000',
                              'image_size': '当前输入视图[width,height]整数', 'bbox': '本条声明坐标制的四个数'}],
                'grounding': {'table': '表名', 'rows': '原行标签', 'columns': '完整列路径', 'scope': '包含/排除层级'},
                'scope_check': {'complete': '布尔值', 'excluded': '明确排除项数组'},
                'items': [{'value': '与答案逐项同序', 'row': '行名', 'column': '列名', 'level': '分类/子类/合计'}],
                'requirements': '列表必须有items和scope_check；单值提取有row/column anchors；不要输出推理'}
            if q.route == 'lookup':
                payload['response_contract']['anchors'] = {
                    name: {'page': '输入页码整数', 'bbox_format': 'pixel（推荐）/norm999/norm1000',
                           'image_size': '当前输入视图[width,height]整数',
                           'bbox': '原行标签框' if name == 'row' else '完整原列标题框'}
                    for name in ('row', 'column')}
            if q.route in {'calculation', 'filter'}:
                payload['response_contract']['verification'] = {
                    'op': '受控操作；求名称极值用argmax/argmin，不用max/min',
                    'values': ['所有候选原文数值'], 'labels': ['与数值等长的原行名'],
                    'separator': '仅string答案且可能并列时使用，例如逗号空格'}
            if q.route == 'enumeration':
                payload['response_contract']['requirements'] += '；纯枚举不是算术，不提供虚构verification；分类/子类按原表顺序，合计单独声明'
        payload['input_views'] = [{'page': p['page'], 'local_view': bool(p.get('view_to_page')),
                                  'width': p['width'], 'height': p['height'],
                                  **({'caption_focused':True,
                                      'scope':'原PDF的竖排表格题注已随页面转正并放大；仅逐字读取本图题注，不是列标题，不从题干补字'}
                                     if p.get('caption_focused') else {}),
                                  **({'orientation_clockwise_degrees':p['orientation']['clockwise_degrees'],
                                      'direction_reference':'本页已按文字主方向转正；题干的原PDF左/右/上/下方位须结合该旋转理解，页码不变；证据仍填当前上传图坐标'}
                                     if (p.get('orientation') or {}).get('clockwise_degrees') else {}),
                                  **({'embedded_raster':True,
                                      'raster_scope':'题注唯一匹配的嵌入图像；不能假设存在单元格文字层或把正文前值当作图内单元格'}
                                     if p.get('embedded_raster') else {}),
                                  **({'table_focused': True,
                                      'scope': '完整表格边界视图，表外标题/脚注已排除；表头换行不等于多个逻辑行'}
                                     if p.get('table_focused') else {}),
                                  **({'outside_table_context': p['outside_table_context'],
                                      'context_scope': '来自PDF文本层的表外上下文，仅帮助理解标题/单位/脚注，不是单元格，不计行列数；符号可能存在字体编码误差'}
                                     if p.get('outside_table_context') else {})} for p in pages]
        if q.question_type == 'structure':
            layout_hints = [{'page': p['page'], **p['pdf_layout_hint']} for p in pages if p.get('pdf_layout_hint')]
            if layout_hints:
                payload['pdf_layout_hints'] = layout_hints
            hints = [{'page': p['page'], **hint} for p in pages if (hint := simple_grid_hint(p))]
            if hints:
                payload['local_grid_hints'] = hints
            columns = [hint for p in pages if (hint := simple_pdf_column_hint(p))]
            if columns:
                payload['pdf_simple_column_hints'] = columns
            payload['response_contract'] = {
                'answer': {'row_count': '完整行数:正整数', 'col_count': '完整列数:正整数',
                           'cells': [{'text': '格内原文或空字符串', 'row': '0起逻辑整数',
                                      'col': '0起逻辑整数', 'rowspan': '正整数', 'colspan': '正整数'}]},
                'evidence': [{'page': '输入页号:整数', 'bbox_format': 'pixel（推荐）/norm999/norm1000',
                              'image_size': '当前输入视图[width,height]整数', 'bbox': '本条声明坐标制的四个数'}],
                'grounding': {'table': '目标表', 'rows': '输出范围', 'columns': '输出范围', 'scope': '局部或完整结构'},
                'full_table_check': {'scope': 'full_table', 'row_count': '与answer一致:整数',
                                     'col_count': '与answer一致:整数', 'page': '目标起始页:整数',
                                     'basis': '计数依据含首尾数据行',
                                     'row_breakdown': {'header': '非负整数', 'body': '非负整数', 'notes': '非负整数'}}}
            payload['response_contract']['requested_scope'] = structure_scope(q)
            payload['response_contract']['full_table_check']['body_range'] = {
                'first': '首个数据行原标签', 'last': '末个数据行原标签'}
            payload['response_contract']['full_table_check']['header_ranges'] = '仅纵向多表：[[start,end],...]，0起半开区间，合计长度等于header'
            payload['response_contract']['full_table_check']['empty_body'] = '仅原表确实没有数据行时为true，否则不得用来逃避整表计数'
    if repair is not None:
        payload["previous"] = ({'issues': repair.get('issues', []),
                                'requires_calculation': repair.get('requires_calculation', False)}
                               if cfg and cfg['run'].get('single_pass', False) else repair)
    if (cfg and cfg['run'].get('single_pass') and cfg['processing'].get('verify_pdf_evidence')
            and q.route == 'filter' and answer_contract(q) == 'string'):
        names = pdf_row_name_hints(pages)
        if names:
            payload['pdf_row_name_hints'] = names
    response_mode = None
    vector_hints = []
    if cfg and cfg['run'].get('single_pass') and cfg['run'].get('program_assembly', False):
        response_mode, contract = facts_contract(q)
        if response_mode:
            payload['response_mode'], payload['response_contract'] = response_mode, contract
            if response_mode == 'header_facts':
                payload['task_requirements']['header_selection'] = (
                    '按上下顺序识别目标表全部表头行带，不自行选答案或扫描行；程序从最上方真实表头行按方向取值。'
                    '部分列的父分组仍是header，不是title；只有跨整表的标题/分区名可标title/section_title。'
                    '空白角格保留，不能漏上层分组、把父/叶文字合在同一格或只报叶子层。'
                    '只输出header_grid.bands，不返回网格尺寸/跨度、answer、task_checks、interpretation或推理。')
                payload['task_requirements']['header_scope'] = '完整表头含全部层级；同格换行完整保留，不混合不同表的行带。'
            if q.question_type == 'structure':
                if (structure_scope(q) == 'header_only'
                        and cfg['run'].get('structure_target_policy', 'as_requested') == 'first'):
                    vector_hints = [g for p in pages if not p.get('view_to_page') for g in p.get('pdf_vector_grids', [])]
                if vector_hints:
                    payload['pdf_vector_grid_hints'] = vector_hints
                    payload['response_contract'] = {k:v for k,v in contract.items() if k in {'evidence','grounding'}}
                    payload['response_contract'].update(
                        table_extent={'page':'目标页整数','grid_id':'唯一目标grid_id',
                                      'body_row_source':'必须为字面量pdf_vector_grid；不再声明尺寸/行标签'},
                        cells=[{'grid_cell_id':'选中网格的每个header_cells ID，完整且仅一次',
                                'text':'按原图填写该几何格内的原文或空字符串；不得把化学元素Ti读成Tr'}])
                    payload['response_contract']['requested_scope'] = structure_scope(q)
                contract['cells_scope'] = structure_scope(q)
                payload['structure_scopes'] = {'table_extent': 'full_table_including_all_body_rows',
                                               'cells': structure_scope(q)}
                payload['target_policy'] = cfg['run'].get('structure_target_policy', 'as_requested')
                if payload.get('pdf_layout_hints') and response_mode == 'table_facts':
                    contract['table_extent']['body_row_source'] = 'pdf_layout_rows'
                    contract['table_extent']['data_row_labels'] = (
                        '条件字段：body_row_source=model时必须返回完整非空标签数组；'
                        'body_row_source=pdf_layout_rows时必须省略此字段，不返回空数组')
                    payload['table_extent_template'] = {
                        'page': payload['pdf_layout_hints'][0]['page'],
                        'col_count': '替换为完整列数整数', 'header_rows': '替换为表头高度整数',
                        'body_row_source': 'pdf_layout_rows', 'note_rows': 0}
    if cfg and cfg['run'].get('single_pass') and response_mode != 'header_facts' and 'header_scope' in payload['task_requirements']:
        payload['response_contract']['interpretation'] = {
            'blank_corner': '真实角格空白时为true，否则false',
            'nearest_right': {'text': '最左表头为空时同一表头行向右最近非空原文', 'same_row': '确认同一表头行时true；非下方数据行或行标签'},
            'column_scope': 'data_fields或page_layout', 'basis': '简短原图定位事实，不输出推理'}
    if cfg and cfg['run'].get('single_pass') and q.question_type == 'structure':
        requested = requested_cell_ranges(q)
        if requested:
            payload['response_contract']['requested_cell_ranges'] = requested
            payload['task_requirements']['partial_structure'] = '前N行与前M列取联合范围，第一列包括全部数据行；不是只输出表头，不能输出联合范围以外的独立格。'
    if cfg and cfg['run'].get('single_pass') and response_mode != 'header_facts' and (checks := task_check_contract(q)):
        payload['response_contract']['task_checks'] = checks
    if cfg and cfg['run'].get('single_pass') and answer_contract(q) == 'number' and response_mode is None:
        payload['response_contract']['numeric_format'] = 'standard/decimal_comma/dot_grouping；无地区格式时省略'
        payload['response_contract']['numeric_format_basis'] = '非standard时必填：原图语言/同列格式/单位依据'
    if response_mode == 'header_facts':
        details, descriptions = header_detail_views(pages,model,cfg,len(extra_images or []))
        if details:
            extra_images = (extra_images or []) + details
            payload['header_detail_views'] = descriptions
    raw = model.ask("repair" if repair is not None else "direct", payload,
                    [p["image"] for p in pages] + (extra_images or []), fresh=fresh)
    assembly_mode = 'legacy_response'
    if response_mode:
        raw, assembly_mode = assemble_response(q, raw, response_mode, payload.get('pdf_layout_hints'), vector_hints)
    header_alignment = None
    header_span_alignment = None
    if q.question_type == 'structure' and payload.get('pdf_layout_hints'):
        # 先校验模型网格，避免重锚点掩盖原始越界或重叠错误。
        validate_candidate_answer(q, raw.get('answer'))
        raw, header_alignment = align_pdf_header(raw, payload['pdf_layout_hints'])
        if header_alignment and getattr(model, 'trace', None):
            model.trace.emit('pdf_header_alignment', **header_alignment)
        raw,header_span_alignment=align_pdf_header_rowspans(raw,pages)
        if header_span_alignment and getattr(model,'trace',None):
            model.trace.emit('pdf_header_span_alignment',**header_span_alignment)
    raw = apply_answer_conventions(q, raw, cfg.get('answer', {})) if cfg else raw
    answer = raw.get("answer")
    validate_candidate_answer(q, answer)
    scope_warnings, task_audit = audit_task_scope(q, raw) if cfg and cfg['run'].get('single_pass') else ([], {})
    array_check = None
    if cfg and cfg['run'].get('single_pass') and cfg['processing'].get('verify_pdf_evidence'):
        validate_full_row_label(q, answer, pages)
        validate_prior_array(q, raw, pages)
        validate_field_semantics(q, raw, pages)
        array_check = validate_grouped_pdf_array(q, raw, pages)
    grid_hint_conflict = False
    layout_hint_conflict = False
    if cfg and cfg['run'].get('single_pass', False) and q.question_type == 'structure':
        check = raw.get('full_table_check')
        counts = check.get('row_breakdown') if isinstance(check, dict) else None
        if not isinstance(check, dict) or not isinstance(counts, dict):
            raise PipelineError('结构协议缺少full_table_check和row_breakdown；须在同次响应核对整表后填写')
        if (any(type(counts.get(k)) is not int or counts[k] < 0 for k in ('header', 'body', 'notes'))
                or any(type(check.get(k)) is not int or check[k] != answer[k] for k in ('row_count', 'col_count'))
                or sum(counts[k] for k in ('header', 'body', 'notes')) != answer['row_count']):
            raise PipelineError('结构尺寸自相矛盾：answer尺寸、full_table_check尺寸和header+body+notes须一致；表外脚注不计')
        validate_structure_scope(q, answer, check)
        if check.get('header_regions') and not check.get('nested_groups'):
            scope_warnings.append('局部嵌套表头缺少外层组及数据范围声明，尚未验证重复表头完整性')
        source_columns = [h for h in payload.get('pdf_simple_column_hints', []) if h['page'] == check.get('page')]
        if len(source_columns) == 1 and answer['col_count'] != source_columns[0]['col_count']:
            raise PipelineError('结构列数与完整PDF单层表头不一致：可能遗漏最右列；不自动补造表头')
        hint = next((h for h in payload.get('local_grid_hints', []) if h['page'] == check.get('page')), None)
        if hint and (answer['row_count'] != hint['row_count'] or counts != hint['row_breakdown']):
            grid_hint_conflict = True
        layout = next((h for h in payload.get('pdf_layout_hints', []) if h['page'] == check.get('page')), None)
        if layout and counts['body'] != layout['body_text_row_count']:
            layout_hint_conflict = True
    if repair is not None and q.question_type == "structure":
        dimensions = {(c["answer"]["row_count"], c["answer"]["col_count"])
                      for c in repair.get("candidates", {}).values()
                      if isinstance(c.get("answer"), dict) and "row_count" in c["answer"] and "col_count" in c["answer"]}
        current = (answer["row_count"], answer["col_count"])
        if len(dimensions) == 1 and current not in dimensions:
            revision = raw.get("dimension_revision")
            valid = (isinstance(revision, dict) and revision.get("scope") == "full_table"
                     and revision.get("previous") == list(next(iter(dimensions)))
                     and revision.get("current") == list(current)
                     and isinstance(revision.get("reason"), str) and bool(revision["reason"].strip()))
            breakdown = revision.get("row_breakdown", {}) if isinstance(revision, dict) else {}
            counts = [breakdown.get(k) for k in ("header", "body", "notes")] if isinstance(breakdown, dict) else []
            if not valid or len(counts) != 3 or any(type(v) is not int or v < 0 for v in counts) or sum(counts) != current[0]:
                raise PipelineError("修复改变一致的整表尺寸，缺少 dimension_revision 与完整行数分解")
    warnings, evidence = list(scope_warnings), []
    raw_evidence = raw.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        warnings.append("缺少有效证据列表，保留答案并回看全页")
    else:
        for item in raw_evidence:
            try:
                evidence.extend(validate_evidence([item], pages, cfg, warnings))
            except (PipelineError, TypeError) as exc:
                warnings.append(f"证据坐标不可用，保留答案并回看全页: {exc}")
    emit_warnings(model, "repair" if repair is not None else "direct", warnings)
    if grid_hint_conflict:
        warnings.append('结构尺寸与本地三线表启发式计数冲突，保留答案但需人工/后续核验；不按提示自动改值')
    if layout_hint_conflict:
        warnings.append('结构数据行数与PDF右侧文字基线计数不一致；该计数仅为启发式，保留不确定，不自动改尺寸')
    anchors = {}
    if cfg and cfg['run'].get('single_pass', False):
        for name, raw_anchor in (raw.get('anchors') or {}).items() if isinstance(raw.get('anchors') or {}, dict) else []:
            if name not in {'row', 'column'}:
                continue
            try:
                anchors[name] = validate_evidence([raw_anchor], pages, cfg, warnings)[0]
            except (PipelineError, TypeError) as exc:
                warnings.append(f'{name}锚点不可用: {exc}')
    for item in list(anchors.values()):
        page = next(p for p in pages if p['page'] == item['page'])
        if page.get('view_to_page'):
            item['bbox'] = transform_box(item['bbox'], page['view_to_page'])
    for item in evidence:
        page = next(p for p in pages if p['page'] == item['page'])
        if page.get('view_to_page'):
            item['bbox'] = transform_box(item['bbox'], page['view_to_page'])
    computed_ok = False
    checks = []
    single_calculation = bool(cfg and cfg['run'].get('single_pass', False) and q.route in {'calculation', 'filter'})
    if single_calculation or (repair is not None and (repair.get("requires_calculation") or q.route in {"calculation", "filter"} or (not q.route and q.question_type == "thinking"))):
        checks = verification_plans(raw)
        if not isinstance(answer, list) and len(checks) != 1:
            raise PipelineError("标量答案不能对应多个复算计划")
        computed = [calculate(p, p.get("values"), p.get("labels")) for p in checks]
        # 单个 select/sort 计划本身已返回数组，不再额外包一层。
        result = computed[0] if len(computed) == 1 and isinstance(computed[0], list) else computed if isinstance(answer, list) else computed[0]
        if canonical(q, result) != canonical(q, answer):
            raise PipelineError("修复答案与程序复算不一致")
        computed_ok = True
    name_check = validate_pdf_name_labels(checks, payload.get('pdf_row_name_hints', []), raw.get('records'))
    candidate = {"answer": answer, "evidence": evidence, "computation_checked": computed_ok,
            'numeric_format': raw.get('numeric_format', 'standard'),
            'answer_normalization': raw.get('answer_normalization', []),
            'task_scope_audit': task_audit,
            'pdf_name_check': name_check,
            'pdf_array_check': array_check,
            'extreme_domain_check': raw.get('extreme_domain_check'),
            'assembly_mode': assembly_mode,
            'fact_wrapper_removed': raw.get('fact_wrapper_removed'),
            'pdf_grid_check': raw.get('pdf_grid_check'),
            'source_records': raw.get('records'),
            'header_alignment': header_alignment,
            'header_span_alignment': header_span_alignment,
            'header_grid_check': raw.get('header_grid_check'),
            'program_excluded_records': raw.get('program_excluded_records', []),
            "anchors": anchors,
            "verification": checks if computed_ok else raw.get('verifications') or ([raw['verification']] if 'verification' in raw else []),
            "items": raw.get('items'), "scope_check": raw.get('scope_check'), "facts": raw.get('facts'),
            "full_table_check": raw.get('full_table_check'),
            "dimension_revision": raw.get("dimension_revision"), "grounding": raw.get("grounding"),
            "warnings": warnings, "evidence_status": "valid" if evidence and not warnings else "partial" if evidence else "unlocalized"}
    if (cfg and cfg['run'].get('single_pass') and cfg['processing'].get('verify_pdf_evidence')
            and cfg['run'].get('localize_pdf_evidence', False)):
        location = localize_pdf_value(q, candidate, pages)
        candidate['pdf_localization'] = location
        if location['status'] == 'supported':
            location.update(model_evidence=evidence, model_anchors=anchors, model_warnings=warnings,
                            model_grounding=candidate.get('grounding'))
            # 仅解除已由文字层定位解决的空间警告，不吞掉其他协议/语义风险。
            remaining = [w for w in warnings if not w.startswith(('缺少有效证据列表', '证据坐标不可用',
                         'bbox 轻微越界', 'row锚点不可用:', 'column锚点不可用:'))]
            candidate.update(evidence=location['evidence'], anchors=location['anchors'],
                             grounding={**candidate['grounding'], 'rows': location['row_text'],
                                        'columns': location['column_text']},
                             warnings=remaining, evidence_status='partial' if remaining else 'valid')
        elif location['status'] in {'value_mismatch', 'unsupported_value'}:
            candidate['warnings'].append('PDF行列交点未支持答案数字；不改值，保留不确定')
        if getattr(model, 'trace', None) and location['status'] != 'not_applicable':
            model.trace.emit('pdf_evidence_localization', **location)
    return candidate
