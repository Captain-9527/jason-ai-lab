import unittest
from table_qa.schemas import Question, PipelineError
from table_qa.validation.grounding import validate_full_row_label


class FullRowLabelTest(unittest.TestCase):
    def test_visible_label_is_required_only_for_unqualified_whole_row(self):
        q = Question('s', 's.pdf', 'extract', '请从表中提取：Group A 的 Summary 行。',
                     answer_format='json_array')
        pages = [{'table_focused': True, 'view_bbox': [0, 0, 300, 200],
                  'pdf_words': [{'text': 'Summary', 'bbox': [10, 10, 100, 20]}]}]
        with self.assertRaisesRegex(PipelineError, '遗漏PDF可见行标签'):
            validate_full_row_label(q, ['15', '20.00'], pages)
        validate_full_row_label(q, ['Summary', '15', '20.00'], pages)
        validate_full_row_label(q, ['15', '20.00'], [])  # 无文字层不猜标签。
        validate_full_row_label(q, ['15', '20.00'], [{**pages[0], 'table_focused': False}])
        validate_full_row_label(q, ['15', '20.00'], [{**pages[0], 'view_bbox': [150, 0, 300, 200]}])
        selected = Question('s', 's.pdf', 'extract', '提取 Summary 行的 A、B 值。',
                            answer_format='json_array')
        validate_full_row_label(selected, ['15', '20.00'], pages)
