"""事实单源组装的合成回归，不包含比赛答案。"""
import copy
import unittest

from table_qa.schemas import Question, PipelineError
from table_qa.solving.assembly import assemble_response
from table_qa.solving.direct import solve_direct
from table_qa.config import load_config


class AssemblyTest(unittest.TestCase):
    def test_table_dimensions_have_one_source_and_keep_header_only(self):
        q = Question('s', 's.png', 'structure', '恢复表头')
        raw = {'table_extent': {'page': 1, 'col_count': 2, 'header_rows': 1,
                               'data_row_labels': ['A', 'B', 'B'], 'note_rows': 0},
               'cells': [{'text': '名称', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 2}]}
        before = copy.deepcopy(raw)
        value, mode = assemble_response(q, raw, 'table_facts')
        self.assertEqual(mode, 'program_assembled')
        self.assertEqual(value['answer']['row_count'], 4)
        self.assertEqual(value['full_table_check']['row_breakdown']['body'], 3)
        self.assertEqual(raw, before)
        with self.assertRaises(PipelineError):
            assemble_response(q, {**raw, 'answer': {'row_count': 1}}, 'table_facts')

    def test_enumeration_keeps_duplicate_values_and_provenance(self):
        q = Question('s', 's.png', 'extract', '各分类金额', answer_format='json_array')
        records = [{'value': '8.50', 'row': r, 'column': '金额', 'level': level}
                   for r, level in [('主类', '主类'), ('其中子类', '子类')]]
        value, _ = assemble_response(q, {'records': records}, 'enumeration_facts')
        self.assertEqual(value['answer'], ['8.50', '8.50'])
        self.assertEqual([v['level'] for v in value['items']], ['主类', '子类'])

    def test_all_tied_winners_are_generated_without_model_answer(self):
        q = Question('s', 's.png', 'thinking', '最高名称', answer_format='string', route='filter')
        records = [{'value': n, 'label': name, 'row': name, 'column': '比率', 'level': '数据'}
                   for name, n in [('A', '.7'), ('B', '.9'), ('C', '.9')]]
        raw = {'records': records, 'operations': [{'op': 'argmax', 'separator': ', '}],
               'evidence': [{'page': 1, 'bbox': [0, 0, 99, 99]}]}
        value, _ = assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(value['answer'], 'B, C')
        cfg = load_config()
        class Model:
            def ask(self, *args, **kwargs): return raw
        result = solve_direct(q, [{'page': 1, 'width': 100, 'height': 100, 'image': 'unused'}], Model(), cfg=cfg)
        self.assertEqual(result['answer'], 'B, C')
        self.assertTrue(result['computation_checked'])
        self.assertEqual(result['assembly_mode'], 'program_assembled')

    def test_partial_extreme_indices_are_checked_against_all_records(self):
        q=Question('s','s.png','thinking','最高名称',answer_format='string',route='filter')
        raw={'records':[{'value':value,'row':name,'label':name,'column':'Score','level':'数据'}
                        for value,name in [('2','A'),('8','B'),('8','C')]],
             'operations':[{'op':'argmax','indices':[0]}]}
        with self.assertRaisesRegex(PipelineError,'完整候选范围'):
            assemble_response(q,raw,'calculation_facts')
        raw['operations'][0]['indices']=[1]
        with self.assertRaisesRegex(PipelineError,'并列项'):
            assemble_response(q,raw,'calculation_facts')
        raw['operations'][0].update(indices=[1,2],separator=', ')
        value,_=assemble_response(q,raw,'calculation_facts')
        self.assertEqual(value['answer'],'B, C')
        self.assertEqual(value['extreme_domain_check']['compared_record_count'],3)

    def test_invalid_indices_or_mixed_answers_are_not_silently_accepted(self):
        q = Question('s', 's.png', 'thinking', '求和', answer_format='number')
        raw = {'records': [{'value': '2', 'row': 'A', 'column': '值', 'level': '数据'}],
               'operations': [{'op': 'sum', 'indices': [1]}]}
        with self.assertRaises(PipelineError): assemble_response(q, raw, 'calculation_facts')
        with self.assertRaises(PipelineError): assemble_response(q, {**raw, 'answer': '2'}, 'calculation_facts')

    def test_legacy_response_is_not_rewritten(self):
        q = Question('s', 's.png', 'structure', '恢复表头')
        raw = {'answer': {'row_count': 1, 'col_count': 1, 'cells': []}}
        value, mode = assemble_response(q, raw, 'table_facts')
        self.assertEqual(mode, 'legacy_response')
        self.assertIs(value, raw)

    def test_enumeration_excludes_declared_aggregate_and_preserves_audit(self):
        q = Question('s', 's.png', 'extract', '分类有哪些', answer_format='json_array')
        records = [{'value': v, 'row': v, 'column': '类别', 'level': level}
                   for v, level in [('A', '主类'), ('A子类', '子类'), ('合计', '合计')]]
        raw = {'records': records, 'scope_check': {'complete': True, 'excluded': []}}
        value, _ = assemble_response(q, raw, 'enumeration_facts')
        self.assertEqual(value['answer'], ['A', 'A子类'])
        self.assertEqual(value['program_excluded_records'], [records[-1]])
        self.assertEqual(len(raw['records']), 3)
        self.assertEqual(raw['scope_check']['excluded'], [])
        excluded_q = Question('s', 's.png', 'extract', '不含合计的分类有哪些', answer_format='json_array')
        value, _ = assemble_response(excluded_q, raw, 'enumeration_facts')
        self.assertEqual(value['answer'], ['A', 'A子类'])

    def test_heatmap_axes_are_separate_from_category_headers(self):
        q = Question('s', 's.png', 'structure', '恢复热力图表头')
        raw = {'matrix_axes': {'page': 1, 'column_axis_title': 'Kind', 'row_axis_title': 'Metric',
                               'column_labels': ['A', 'B'], 'row_labels': ['R1', 'R2', 'R3']}}
        value, _ = assemble_response(q, raw, 'matrix_facts')
        self.assertEqual(value['answer']['row_count'], 4)
        self.assertEqual(value['answer']['col_count'], 3)
        self.assertEqual([c['text'] for c in value['answer']['cells']], ['', 'A', 'B'])
        raw['matrix_axes']['column_labels'][0] = 'Kind A'
        with self.assertRaisesRegex(PipelineError, '混入独立横轴标题'):
            assemble_response(q, raw, 'matrix_facts')

    def test_empty_data_labels_do_not_automatically_claim_empty_table(self):
        q = Question('s', 's.png', 'structure', '恢复表头')
        raw = {'table_extent': {'page': 1, 'col_count': 2, 'header_rows': 1,
                               'data_row_labels': [], 'note_rows': 0}, 'cells': []}
        with self.assertRaisesRegex(PipelineError, '整表数据行标签为空'):
            assemble_response(q, raw, 'table_facts')

    def test_pdf_rows_require_explicit_same_page_reference_without_duplicate_source(self):
        q = Question('s', 's.pdf', 'structure', '恢复表头行结构')
        raw = {'table_extent': {'page': 1, 'col_count': 1, 'header_rows': 1, 'note_rows': 0,
                               'body_row_source': 'pdf_layout_rows'},
               'cells': [{'text': 'Value', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]}
        hints = [{'page': 1, 'body_rows': ['A 12', 'B 15']}]
        before = copy.deepcopy(raw)
        value, _ = assemble_response(q, raw, 'table_facts', hints)
        self.assertEqual(value['answer']['row_count'], 3)
        self.assertEqual(value['full_table_check']['body_row_source'], 'pdf_layout_rows')
        self.assertEqual(raw, before)
        with self.assertRaises(PipelineError): assemble_response(q, raw, 'table_facts')
        with self.assertRaises(PipelineError): assemble_response(q, raw, 'table_facts', [{'page': 2, 'body_rows': ['A']}])
        wrong = copy.deepcopy(raw); wrong['table_extent']['data_row_labels'] = []
        with self.assertRaises(PipelineError): assemble_response(q, wrong, 'table_facts', hints)
        nullable = copy.deepcopy(raw); nullable['table_extent']['data_row_labels'] = None
        result, _ = assemble_response(q, nullable, 'table_facts', hints)
        self.assertEqual(result['answer']['row_count'], 3)
        self.assertIsNone(nullable['table_extent']['data_row_labels'])
        nullable['table_extent']['body_row_source'] = 'model'
        with self.assertRaises(PipelineError): assemble_response(q, nullable, 'table_facts', hints)

    def test_name_enumeration_does_not_accept_neighboring_numeric_column(self):
        q = Question('s', 's.png', 'extract', '分类有哪些', answer_format='json_array')
        raw = {'records': [{'value': '15.00', 'row': 'A', 'column': '比例', 'level': '分类'}]}
        with self.assertRaisesRegex(PipelineError, '全部为数值'):
            assemble_response(q, raw, 'enumeration_facts')
