"""物理列核对和受限左角格对齐；全为合成数据。"""
import copy
import unittest

from table_qa.schemas import PipelineError
from table_qa.validation.pdf_header import align_pdf_header


class PdfHeaderTest(unittest.TestCase):
    def setUp(self):
        self.raw = {'answer': {'row_count': 4, 'col_count': 3, 'cells': [
            {'text': 'Metric', 'row': 0, 'col': 0, 'rowspan': 2, 'colspan': 1},
            {'text': 'ModelA', 'row': 0, 'col': 1, 'rowspan': 1, 'colspan': 1},
            {'text': 'ModelB', 'row': 0, 'col': 2, 'rowspan': 1, 'colspan': 1},
            {'text': 'Mode', 'row': 1, 'col': 1, 'rowspan': 1, 'colspan': 1},
            {'text': 'Mode', 'row': 1, 'col': 2, 'rowspan': 1, 'colspan': 1}]},
                    'full_table_check': {'page': 1, 'col_count': 3,
                                         'row_breakdown': {'header': 2, 'body': 2, 'notes': 0}}}
        self.hints = [{'page': 1, 'physical_header_columns': ['', 'Metric', 'ModelA Mode', 'ModelB Mode']}]

    def test_insert_only_verified_missing_left_group_column_and_audit(self):
        before = copy.deepcopy(self.raw)
        value, audit = align_pdf_header(self.raw, self.hints)
        self.assertEqual(value['answer']['col_count'], 4)
        self.assertEqual(value['full_table_check']['col_count'], 4)
        self.assertEqual(value['answer']['row_count'], 4)
        self.assertEqual(value['answer']['cells'][0], {'text': '', 'row': 0, 'col': 0, 'rowspan': 2, 'colspan': 1})
        self.assertEqual(audit['original_answer'], before['answer'])
        self.assertEqual(self.raw, before)
        again, audit2 = align_pdf_header(value, self.hints)
        self.assertEqual(again, value)
        self.assertEqual(audit2['shift'], 0)

    def test_duplicate_wrapped_suffix_is_rejected_not_deleted(self):
        raw = copy.deepcopy(self.raw)
        raw['answer']['cells'][1]['text'] += ' Mode'
        with self.assertRaisesRegex(PipelineError, '重复换行后缀'):
            align_pdf_header(raw, self.hints)
        self.assertEqual(raw['answer']['cells'][1]['text'], 'ModelA Mode')

    def test_unknown_dimension_or_swapped_columns_not_guessed(self):
        raw = copy.deepcopy(self.raw)
        raw['answer']['col_count'] = 2
        with self.assertRaises(PipelineError): align_pdf_header(raw, self.hints)
        raw = copy.deepcopy(self.raw)
        raw['answer']['cells'][1]['text'] = 'ModelB'
        with self.assertRaises(PipelineError): align_pdf_header(raw, self.hints)

    def test_missing_geometry_and_complex_colspan_are_not_rewritten(self):
        value, audit = align_pdf_header(self.raw, [])
        self.assertIs(value, self.raw)
        self.assertIsNone(audit)
        raw = copy.deepcopy(self.raw)
        raw['answer']['cells'][1]['colspan'] = 2
        self.assertIsNone(align_pdf_header(raw, self.hints)[1])
