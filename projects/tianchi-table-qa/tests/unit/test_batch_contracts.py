"""合成样例：只兼容无歧义包装与空条件，不放宽事实或有限数值校验。"""
import copy
import unittest

from table_qa.config import load_config
from table_qa.schemas import Question, PipelineError
from table_qa.solving.assembly import assemble_response
from table_qa.solving.calculator import calculate
from table_qa.solving.direct import solve_direct


class BatchContractTest(unittest.TestCase):
    def test_unique_wrapper_unpacked_without_mutating_source(self):
        q = Question('s', 's.pdf', 'structure', '恢复表头')
        inner = {'table_extent': {'page': 1, 'col_count': 1, 'header_rows': 1,
                                 'data_row_labels': ['A', 'B'], 'note_rows': 0},
                 'cells': [{'text': 'H', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]}
        raw = {'evidence': [], 'table_facts': inner}
        before = copy.deepcopy(raw)
        value, _ = assemble_response(q, raw, 'table_facts')
        self.assertEqual(value['answer']['row_count'], 3)
        self.assertEqual(value['fact_wrapper_removed'], 'table_facts')
        self.assertEqual(raw, before)
        for extra in [{'cells': inner['cells']}, {'answer': {}}, {'table_extent': {}}]:
            with self.assertRaisesRegex(PipelineError, '包装层与顶层冲突'):
                assemble_response(q, {**raw, **extra}, 'table_facts')
        for invalid in [{'table_facts': {'answer': {}}},
                        {**raw, 'matrix_facts': {}},
                        {'table_facts': {**inner, 'table_facts': inner}}]:
            with self.assertRaisesRegex(PipelineError, '包装层与顶层冲突'):
                assemble_response(q, invalid, 'table_facts')

    def test_null_optional_predicate_does_not_block_extreme(self):
        q = Question('s', 's.pdf', 'thinking', '最高名称', answer_format='string', route='filter')
        raw = {'records': [{'value': v, 'row': name, 'label': name, 'column': '值', 'level': '数据'}
                           for name, v in [('A', '3'), ('B', '7')]],
               'operations': [{'op': 'argmax', 'predicate': None}]}
        value, _ = assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(value['answer'], 'B')
        self.assertNotIn('predicate', value['verifications'][0])
        self.assertIsNone(raw['operations'][0]['predicate'])
        raw['operations'][0]['predicate'] = {'operator': 'gt', 'value': '-inf'}
        with self.assertRaises(PipelineError):
            assemble_response(q, raw, 'calculation_facts')

    def test_controlled_product_uses_decimal_and_requires_values(self):
        self.assertEqual(calculate({'op': 'product'}, ['2', '3.5']), '7')
        self.assertEqual(calculate({'op': 'product'}, ['0', '9']), '0')
        with self.assertRaises(PipelineError):
            calculate({'op': 'product'}, [])

    def test_rotated_page_direction_metadata_is_sent_without_reasoning(self):
        class Model:
            def ask(self, stage, payload, *args, **kwargs):
                self.payload = payload
                return {'answer': 'Standard', 'evidence': [{'page': 1, 'bbox_format': 'pixel',
                        'image_size': [100, 80], 'bbox': [0, 0, 20, 20]}]}
        model = Model()
        q = Question('s', 's.pdf', 'extract', '原页右上角标准号', answer_format='string', route='lookup')
        page = {'page': 1, 'width': 100, 'height': 80, 'image': 'unused',
                'orientation': {'clockwise_degrees': 90}}
        solve_direct(q, [page], model, cfg=load_config())
        self.assertEqual(model.payload['input_views'][0]['orientation_clockwise_degrees'], 90)
        self.assertNotIn('reasoning_content', str(model.payload))
        page.pop('orientation')
        solve_direct(q, [page], model, cfg=load_config())
        self.assertNotIn('orientation_clockwise_degrees', model.payload['input_views'][0])
