"""合成范围事实，不包含比赛题号、文件名或生产答案。"""
import copy
import unittest

from table_qa.config import load_config
from table_qa.schemas import PipelineError, Question
from table_qa.solving.direct import solve_direct
from table_qa.validation.task_scope import audit_task_scope, task_check_contract, task_requirements


class TaskChecksTest(unittest.TestCase):
    def q(self, text, fmt='string'):
        return Question('synthetic', 'synthetic.png', 'extract', text, answer_format=fmt, route='lookup')

    def test_unreadable_value_does_not_mean_absent_column(self):
        q = self.q('是否含金额列')
        checks = {'presence': [{'field': q.question, 'answer_index': 0, 'label': '总价/币制',
                               'present': True, 'value_readable': False}]}
        self.assertFalse(audit_task_scope(q, {'answer': '是', 'task_checks': checks})[0])
        with self.assertRaisesRegex(PipelineError, '值不可读'):
            audit_task_scope(q, {'answer': '否', 'task_checks': checks})
        checks['presence'][0].update(present=False, label='')
        self.assertFalse(audit_task_scope(q, {'answer': '否', 'task_checks': checks})[0])

    def test_missing_legacy_checks_is_warning_and_invalid_checks_are_rejected(self):
        q = self.q('是否含编号字段')
        self.assertTrue(audit_task_scope(q, {'answer': '是'})[0])
        with self.assertRaisesRegex(PipelineError, '须为对象'):
            audit_task_scope(q, {'answer': '是', 'task_checks': []})
        with self.assertRaisesRegex(PipelineError, '位置不合法'):
            audit_task_scope(q, {'answer': '是', 'task_checks': {'presence': [
                {'field': q.question, 'answer_index': True}]}})

    def test_extreme_in_field_list_checks_all_declared_values(self):
        q = self.q('按表中顺序列出以下字段的答案：最低价格、备注。', 'json_array')
        raw = {'answer': ['90', '原文'], 'task_checks': {'field_extrema': [
            {'field': '最低价格', 'answer_index': 0, 'op': 'min', 'values': ['120', '90', '15'],
             'scope_complete': True, 'excluded': []}]}}
        before = copy.deepcopy(raw)
        with self.assertRaisesRegex(PipelineError, '复算不一致'):
            audit_task_scope(q, raw)
        self.assertEqual(raw, before)
        raw['answer'][0] = '15'
        warnings, audit = audit_task_scope(q, raw)
        self.assertFalse(warnings)
        audit['task_checks']['field_extrema'][0]['values'][0] = 'changed'
        self.assertEqual(raw['task_checks']['field_extrema'][0]['values'][0], '120')
        raw['task_checks']['field_extrema'][0]['excluded'] = ['某一可见项目']
        self.assertTrue(audit_task_scope(q, raw)[0])

    def test_multiple_values_for_one_field_do_not_require_fixed_array_length(self):
        q = self.q('按表中顺序列出以下字段的答案：项目经理、是否含价格列。', 'json_array')
        raw = {'answer': ['甲', '乙', '是'], 'task_checks': {'presence': [
            {'field': '是否含价格列', 'answer_index': 2, 'label': '价格', 'present': True, 'value_readable': True}]}}
        self.assertFalse(audit_task_scope(q, raw)[0])

    def test_row_count_does_not_use_worksheet_index(self):
        q = self.q('提取可见总行数', 'number')
        self.assertEqual(task_check_contract(q)['table_rows']['basis'], 'table_rows')
        raw = {'answer': 3, 'task_checks': {'table_rows': {'basis': 'table_rows', 'row_labels': ['表头', '记录甲', '记录乙']}}}
        self.assertFalse(audit_task_scope(q, raw)[0])
        raw['answer'] = 9
        with self.assertRaisesRegex(PipelineError, '逐行标识'):
            audit_task_scope(q, raw)
        raw['task_checks']['table_rows']['basis'] = 'worksheet_index'
        with self.assertRaisesRegex(PipelineError, '工作表行号'):
            audit_task_scope(q, raw)

    def test_numeric_extreme_does_not_capture_name_or_structure_answers(self):
        self.assertFalse(task_check_contract(self.q('按表中顺序列出以下字段的答案：最高分科目。', 'json_array')))
        q = Question('s', 'synthetic.png', 'structure', '恢复前几行的结构', answer_format='json')
        self.assertFalse(task_check_contract(q))
        self.assertFalse(task_check_contract(self.q('读取普通备注')))

    def test_spatial_rule_compares_positions_not_label_numbers(self):
        rule = task_requirements(self.q('黄色区域中位于上方的编号'), load_config())
        self.assertIn('不比较格内编号大小', rule['spatial_selection'])

    def test_single_call_solver_receives_matching_response_schema(self):
        q = self.q('是否含价格列')
        class Model:
            calls = 0
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls += 1
                declaration = payload['response_contract']['task_checks']['presence']
                self.assertIsInstance(declaration, list)
                self.assertEqual(declaration[0]['field'], q.question)
                return {'answer': '是', 'task_checks': {'presence': [{
                    'field': q.question, 'answer_index': 0, 'label': '价格', 'present': True, 'value_readable': False}]}}
        model = Model()
        result = solve_direct(q, [{'page': 1, 'width': 100, 'height': 100, 'image': 'unused'}], model, cfg=load_config())
        self.assertEqual(model.calls, 1)
        self.assertEqual(result['answer'], '是')
        self.assertTrue(result['task_scope_audit']['task_checks']['presence'][0]['present'])
