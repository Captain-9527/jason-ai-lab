"""合成响应验证协议根因，不将比赛题号/答案写入生产规则。"""
import copy
import unittest

from table_qa.application.routing import classify
from table_qa.config import load_config
from table_qa.schemas import Question, PipelineError
from table_qa.solving.direct import solve_direct, verification_plans
from table_qa.validation.structure_scope import validate_structure_scope
from table_qa.validation.coordinates import coordinate_contract


class SolverContractTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.cfg['model']['bbox_format'] = 'pixel'
        self.pages = [{'page': 1, 'image': 'unused', 'width': 200, 'height': 100}]

    def model(self, raw):
        class Model:
            def ask(inner, stage, payload, *args, **kwargs):
                inner.payload = payload
                return raw
        return Model()

    def test_verification_object_and_array_alias_are_valid_without_mutation(self):
        plan = {'op': 'sum', 'values': ['2', '3']}
        for raw in [{'verification': plan}, {'verification': [plan]}, {'verifications': [plan]}]:
            before = copy.deepcopy(raw)
            self.assertEqual(verification_plans(raw), [plan])
            self.assertEqual(raw, before)
        for raw in [{}, {'verification': []}, {'verification': [[plan]]}, {'verification': 'sum'},
                    {'verification': plan, 'verifications': [plan]}, {'verifications': plan}]:
            with self.assertRaises(PipelineError):
                verification_plans(raw)

    def test_numeric_plan_in_verification_array_no_longer_crashes(self):
        q = Question('synthetic', 'sample.png', 'thinking', '求和', route='calculation', answer_format='number')
        raw = {'answer': '5', 'verification': [{'op': 'sum', 'values': ['2', '3']}]}
        result = solve_direct(q, self.pages, self.model(raw), cfg=self.cfg)
        self.assertTrue(result['computation_checked'])
        self.assertEqual(result['verification'], raw['verification'])

    def test_argmax_keeps_tied_labels_and_rejects_omitted_winner(self):
        q = Question('synthetic', 'sample.png', 'thinking', '找出最高的名称', route='filter', answer_format='string')
        plan = {'op': 'argmax', 'values': ['0.8', '0.9', '0.9'],
                'labels': ['A', 'B', 'C'], 'separator': ', '}
        raw = {'answer': 'B, C', 'verification': [plan]}
        self.assertTrue(solve_direct(q, self.pages, self.model(raw), cfg=self.cfg)['computation_checked'])
        with self.assertRaisesRegex(PipelineError, '复算不一致'):
            solve_direct(q, self.pages, self.model({**raw, 'answer': 'B'}), cfg=self.cfg)

    def test_enumeration_not_forced_through_calculator_and_keeps_duplicates(self):
        for text in ['坏账的分类有哪些？', '各个分类的金额是多少？']:
            q = Question('synthetic', 'sample.png', 'thinking', text, answer_format='json_array')
            route = classify(q, self.cfg)
            self.assertEqual(route['category'], 'enumeration')
        for text in ['哪些天符合条件', '找出金额最高的类别', '哪些金额大于5']:
            self.assertEqual(classify(Question('s', 's.png', 'extract', text), self.cfg)['category'], 'filter')
        q = Question('synthetic', 'sample.png', 'thinking', '分类有哪些', route='enumeration', answer_format='json_array')
        model = self.model({'answer': ['A', 'B', 'B']})
        result = solve_direct(q, self.pages, model, cfg=self.cfg)
        self.assertEqual(result['answer'], ['A', 'B', 'B'])
        self.assertFalse(result['computation_checked'])
        self.assertIn('records', model.payload['response_contract'])
        self.assertNotIn('verification', model.payload['response_contract'])

    def test_header_cells_exclude_body_without_changing_full_dimensions(self):
        q = Question('s', 's.png', 'structure', '恢复表头行结构')
        answer = {'row_count': 9, 'col_count': 2, 'cells': [
            {'text': 'Name', 'row': 0, 'col': 0, 'rowspan': 2, 'colspan': 1},
            {'text': 'Value', 'row': 0, 'col': 1, 'rowspan': 2, 'colspan': 1}]}
        check = {'row_breakdown': {'header': 2, 'body': 7, 'notes': 0}}
        before = copy.deepcopy(answer)
        validate_structure_scope(q, answer, check)
        self.assertEqual(answer, before)
        with self.assertRaisesRegex(PipelineError, '仅要求表头'):
            validate_structure_scope(q, {**answer, 'cells': [{**answer['cells'][0], 'row': 2}]}, check)
        with self.assertRaisesRegex(PipelineError, '仍须统计整表数据行'):
            validate_structure_scope(q, answer, {'row_breakdown': {'header': 9, 'body': 0, 'notes': 0}})

    def test_multitable_header_ranges_keep_absolute_offsets(self):
        q = Question('s', 's.png', 'structure', '恢复多个表的表头')
        answer = {'row_count': 12, 'col_count': 2, 'cells': [
            {'text': 'H1', 'row': 0, 'rowspan': 1}, {'text': 'H2', 'row': 7, 'rowspan': 1}]}
        check = {'row_breakdown': {'header': 2, 'body': 10, 'notes': 0}, 'header_ranges': [[0, 1], [7, 8]]}
        validate_structure_scope(q, answer, check)
        with self.assertRaises(PipelineError):
            validate_structure_scope(q, answer, {**check, 'header_ranges': [[0, 2], [1, 3]]})

    def test_normalized_coordinate_contract_is_explicit_not_guessed(self):
        self.cfg['model']['bbox_format'] = 'norm999'
        c = coordinate_contract(self.cfg)
        self.assertEqual(c['range'], [0, 999])
        self.assertEqual(c['example_full_image'], [0, 0, 999, 999])
