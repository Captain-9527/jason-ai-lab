"""合成结构题：历史success不能压过带整表依据的新候选；不硬编码比赛答案。"""
import copy
import unittest

from table_qa.application.runner import retain_baseline_candidate
from table_qa.schemas import Question
from table_qa.validation.structure_scope import has_full_structure_check
from table_qa.validation.field_semantics import validate_field_semantics
from table_qa.schemas import PipelineError


class RegressionBaselineTest(unittest.TestCase):
    def fixture(self):
        q = Question('s', 'synthetic.png', 'structure', '恢复表头行结构')
        cells = [{'text': '字段', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]
        old = {'status': 'success', 'answer': {'row_count': 1, 'col_count': 1, 'cells': cells}}
        current = {'status': 'uncertain', 'answer': {**old['answer'], 'row_count': 4},
                   'selected_candidate': {'full_table_check': {'scope': 'full_table', 'page': 1,
                       'row_count': 4, 'col_count': 1, 'basis': '三个数据行',
                       'row_breakdown': {'header': 1, 'body': 3, 'notes': 0}}},
                   'warnings': ['定位风险']}
        return q, old, current

    def test_legacy_header_only_success_does_not_override_new_extent(self):
        q, old, current = self.fixture()
        before = copy.deepcopy((old, current))
        value = retain_baseline_candidate(q, current, old, 'base', {}, True)
        self.assertEqual(value['answer']['row_count'], 4)
        self.assertEqual(value['status'], 'uncertain')
        self.assertIn('baseline_not_preferred', value)
        self.assertEqual((old, current), before)

    def test_no_new_extent_or_no_new_answer_keeps_conservative_fallback(self):
        q, old, current = self.fixture()
        for candidate in ({**current, 'selected_candidate': {}}, {**current, 'answer': None}):
            value = retain_baseline_candidate(q, candidate, old, 'base', {}, True)
            self.assertEqual(value['selection_reason'], 'baseline_fallback')

    def test_both_valid_extent_checks_do_not_prefer_larger_dimensions(self):
        q, old, current = self.fixture()
        old['answer']['row_count'] = 2
        old['selected_candidate'] = copy.deepcopy(current['selected_candidate'])
        check = old['selected_candidate']['full_table_check']
        check['row_count'] = 2
        check['row_breakdown']['body'] = 1
        self.assertTrue(has_full_structure_check(q, old))
        value = retain_baseline_candidate(q, current, old, 'base', {}, True)
        self.assertEqual(value['answer']['row_count'], 2)

    def test_header_height_cannot_claim_full_extent(self):
        q, old, current = self.fixture()
        current['selected_candidate']['full_table_check']['row_breakdown'] = {'header': 4, 'body': 0, 'notes': 0}
        self.assertFalse(has_full_structure_check(q, current))

    def test_camera_orientation_cannot_copy_unrelated_confirmation(self):
        q = Question('s', 'synthetic.png', 'extract', '是否横向拍摄？')
        with self.assertRaisesRegex(PipelineError, '无关是非字段'):
            validate_field_semantics(q, {'answer': '否', 'grounding': {'rows': '特殊关系确认'}}, [])
        validate_field_semantics(q, {'answer': '是', 'grounding': {'scope': '图片宽大于高，横向'}}, [])
        validate_field_semantics(q, {'answer': '是', 'grounding': {'scope': '整张图像'},
                                    'items': [{'row': '图像属性', 'column': '拍摄方向'}]}, [])

