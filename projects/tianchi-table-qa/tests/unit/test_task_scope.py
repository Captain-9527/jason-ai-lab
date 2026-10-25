"""合成题意范围与区间歧义回归，不包含比赛题号或参考答案。"""
import copy
import unittest

from table_qa.config import load_config
from table_qa.schemas import Question, PipelineError
from table_qa.solving.assembly import facts_contract
from table_qa.solving.direct import solve_direct
from table_qa.validation.task_scope import task_requirements, audit_task_scope, apply_answer_conventions
from table_qa.validation.answer import format_answer
from table_qa.validation.structure_scope import requested_cell_ranges, validate_structure_scope


class TaskScopeTest(unittest.TestCase):
    def q(self, text, route='lookup', kind='extract'):
        return Question('s', 'sample.png', kind, text, answer_format='string', route=route)

    def test_email_is_rejected_not_silently_changed(self):
        for text in ['读取邮箱', '提取 email_id']:
            for answer in ['reader @site.test', 'reader@ site.test']:
                raw = {'answer': answer}
                with self.assertRaisesRegex(PipelineError, '内部空格'):
                    audit_task_scope(self.q(text), raw)
                self.assertEqual(raw['answer'], answer)
        self.assertEqual(audit_task_scope(self.q('读取邮箱'), {'answer': 'reader@site.test'}), ([], {}))
        self.assertEqual(audit_task_scope(self.q('读取备注'), {'answer': 'name @ site'}), ([], {}))

    def test_blank_header_is_warning_not_fake_success(self):
        warnings, audit = audit_task_scope(self.q('最左侧的表头是什么'), {
            'answer': '空白', 'interpretation': {'blank_corner': True}})
        self.assertTrue(any('空白' in warning for warning in warnings))
        self.assertTrue(audit['interpretation']['blank_corner'])
        with self.assertRaisesRegex(PipelineError, '排版栏数'):
            audit_task_scope(self.q('主表列数'), {'answer': 2, 'interpretation': {'column_scope': 'page_layout'}})

    def test_range_sensitivity_keeps_both_counts_without_mutating_plan(self):
        plan = {'op': 'count', 'values': ['2~3', '0.5-2.5', '—'],
                'predicate': {'operator': 'gt', 'value': '1.5', 'missing': 'exclude', 'range_policy': 'all'}}
        raw = {'answer': '1', 'verifications': [plan]}
        before = copy.deepcopy(raw)
        warnings, audit = audit_task_scope(self.q('含量大于1.5的有多少个', 'filter'), raw)
        self.assertIn('口径', warnings[0])
        self.assertEqual(audit['range_sensitivity'][0]['all'], '1')
        self.assertEqual(audit['range_sensitivity'][0]['any'], '2')
        self.assertEqual(raw, before)
        warnings, _ = audit_task_scope(self.q('整个区间含量大于1.5的有多少个', 'filter'), raw)
        self.assertFalse(warnings)

    def test_task_requirements_are_semantic_and_preserve_literal_fields(self):
        cfg = load_config()
        task = task_requirements(self.q('按顺序列出以下字段的答案：小表名称、流量、单位。', 'field_list'), cfg)
        self.assertEqual(task['requested_fields'], ['小表名称', '流量', '单位'])
        self.assertNotIn('sample.png', str(task))
        self.assertIn('小数逗号', task['literal'])
        mode, contract = facts_contract(self.q('大于5的有多少个', 'filter'))
        self.assertEqual(mode, 'calculation_facts')
        self.assertIn('range_policy', contract['operations'][0]['predicate'])

    def test_blank_header_audit_survives_single_call_solver(self):
        cfg = load_config()
        cfg['run']['program_assembly'] = False  # 显式覆盖旧响应兼容路径；新事实路径另有回归。
        class Model:
            calls = 0
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls += 1
                self.assertIn('interpretation', payload['response_contract'])
                return {'answer': '空白', 'interpretation': {'blank_corner': True}}
        model = Model()
        result = solve_direct(self.q('最左侧的表头是什么'), [
            {'page': 1, 'width': 100, 'height': 100, 'image': 'unused'}], model, cfg=cfg)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result['answer'], '空白')
        self.assertTrue(result['task_scope_audit']['interpretation']['blank_corner'])
        self.assertTrue(any('空白' in w for w in result['warnings']))

    def test_partial_structure_union_requires_data_column_and_excludes_unrequested_cells(self):
        q = self.q('恢复前两行和第一列', kind='structure')
        self.assertEqual(requested_cell_ranges(q), {'leading_rows': 2, 'leading_columns': 1, 'operator': 'union'})
        cells = [{'text': f'{r}/{c}', 'row': r, 'col': c, 'rowspan': 1, 'colspan': 1}
                 for r in range(4) for c in range(3) if r < 2 or c == 0]
        answer = {'row_count': 4, 'col_count': 3, 'cells': cells}
        validate_structure_scope(q, answer, {})
        with self.assertRaisesRegex(PipelineError, '遗漏'):
            validate_structure_scope(q, {**answer, 'cells': cells[:-1]}, {})
        with self.assertRaisesRegex(PipelineError, '未请求'):
            validate_structure_scope(q, {**answer, 'cells': cells + [
                {'text': 'outside', 'row': 3, 'col': 2, 'rowspan': 1, 'colspan': 1}]}, {})
        self.assertIsNone(requested_cell_ranges(self.q('恢复前两行和第一列的交点', kind='structure')))

    def test_blank_left_header_uses_nearest_right_in_same_header_row(self):
        q = self.q('最左侧的表头是什么')
        raw = {'answer': None, 'interpretation': {'blank_corner': True,
               'nearest_right': {'text': 'Right header', 'same_row': True}}}
        before = copy.deepcopy(raw)
        normalized = apply_answer_conventions(q, raw, {})
        self.assertEqual(normalized['answer'], 'Right header')
        self.assertEqual(normalized['answer_normalization'][0]['rule'], 'blank_left_header_nearest_right')
        warnings = audit_task_scope(q, normalized)[0]
        self.assertTrue(any('表头扫描' in warning for warning in warnings))
        self.assertFalse(any('空白表头尚未' in warning for warning in warnings))
        self.assertEqual(raw, before)
        with self.assertRaisesRegex(PipelineError, '不能取下方'):
            apply_answer_conventions(q, {**raw, 'answer': 'First data row'}, {})
        raw['interpretation']['nearest_right']['same_row'] = False
        self.assertIsNone(apply_answer_conventions(q, raw, {})['answer'])

    def test_legacy_downward_header_declaration_is_not_accepted(self):
        q = self.q('最左侧的表头是什么')
        raw = {'answer': 'First data row', 'interpretation': {'blank_corner': True,
               'nearest_below': {'text': 'First data row', 'same_column': True}}}
        value = apply_answer_conventions(q, raw, {})
        self.assertEqual(value, raw)
        self.assertTrue(audit_task_scope(q, value)[0])

    def test_right_neighbor_rule_does_not_change_nonblank_or_rightmost_header(self):
        for text, blank in [('最左侧的表头是什么', False), ('最右侧的表头是什么', True)]:
            raw = {'answer': 'Original', 'interpretation': {'blank_corner': blank,
                   'nearest_right': {'text': 'Neighbor', 'same_row': True}}}
            self.assertEqual(apply_answer_conventions(self.q(text), raw, {}), raw)

    def test_header_solver_receives_right_neighbor_contract_and_normalizes_once(self):
        cfg = load_config()
        cfg['run']['program_assembly'] = False
        class Model:
            calls = 0
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls += 1
                contract = payload['response_contract']['interpretation']
                self.assertIn('nearest_right', contract)
                self.assertNotIn('nearest_below', contract)
                self.assertIn('同一表头行向右', payload['task_requirements']['header_scope'])
                return {'answer': None, 'interpretation': {'blank_corner': True,
                        'nearest_right': {'text': 'Neighbor', 'same_row': True}}}
        model = Model()
        result = solve_direct(self.q('最左侧的表头是什么'), [
            {'page': 1, 'width': 100, 'height': 100, 'image': 'unused'}], model, cfg=cfg)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result['answer'], 'Neighbor')
        self.assertTrue(any('表头扫描' in warning for warning in
                            audit_task_scope(self.q('最左侧的表头是什么'), result)[0]))

    def test_structure_blank_header_cell_is_not_replaced_by_neighbor(self):
        q = self.q('恢复最左侧表头的结构', kind='structure')
        raw = {'answer': {'row_count': 2, 'col_count': 2, 'cells': [
            {'text': '', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]},
            'interpretation': {'blank_corner': True,
                               'nearest_right': {'text': 'Neighbor', 'same_row': True}}}
        self.assertEqual(apply_answer_conventions(q, raw, {}), raw)
        self.assertFalse(audit_task_scope(q, raw)[0])

    def test_array_null_policy_does_not_mutate_or_convert_whole_missing_answer(self):
        from dataclasses import replace
        q = replace(self.q('按顺序列出以下字段'), answer_format='json_array')
        policy = {'array_nulls': 'empty_string'}
        raw = {'answer': ['7', None, 'null', '0'], 'items': [{'value': v} for v in ['7', None, 'null', '0']]}
        before = copy.deepcopy(raw)
        value = apply_answer_conventions(q, raw, policy)
        self.assertEqual(value['answer'], ['7', '', '', '0'])
        self.assertEqual([v['value'] for v in value['items']], value['answer'])
        self.assertEqual(format_answer(q, raw['answer'], policy), '["7","","","0"]')
        self.assertEqual(raw, before)
        for answer in (None, [None], [True], [{'value': None}]):
            if answer == [None]:
                with self.assertRaises(PipelineError):
                    format_answer(q, answer)  # 旧快照不自动放宽。
            else:
                with self.assertRaises(PipelineError):
                    format_answer(q, answer, policy)
