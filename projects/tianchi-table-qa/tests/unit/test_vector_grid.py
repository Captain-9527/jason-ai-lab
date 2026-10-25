"""合成矢量表：几何只决定网格，不猜文字或目标表。"""
import copy
import unittest

from table_qa.processing.vector_grid import grid_from_boxes
from table_qa.schemas import Question, PipelineError
from table_qa.solving.assembly import assemble_response
from table_qa.validation.field_semantics import validate_field_semantics


class VectorGridTest(unittest.TestCase):
    def setUp(self):
        self.boxes = [[0, 0, 10, 20], [10, 0, 20, 20], [20, 0, 40, 10],
                      [20, 10, 30, 20], [30, 10, 40, 20], [0, 40, 40, 50]]
        self.boxes += [[x, y, x+10, y+10] for y in (20, 30) for x in (0, 10, 20, 30)]
        self.grid = grid_from_boxes(self.boxes, 1, 'p1-g0')

    def test_complete_geometry_and_note_rows(self):
        grid = self.grid
        self.assertEqual((grid['row_count'], grid['col_count'], grid['header_rows'], grid['note_rows']), (5, 4, 2, 1))
        self.assertEqual(len(grid['header_cells']), 5)
        self.assertIsNone(grid_from_boxes(self.boxes[1:], 1, 'bad'))
        self.assertIsNone(grid_from_boxes(self.boxes+[self.boxes[0][0:2]+[20, 20]], 1, 'overlap'))

    def test_grid_reference_is_the_only_dimension_source(self):
        q = Question('s', 's.pdf', 'structure', '恢复表头行结构')
        raw = {'table_extent': {'page': 1, 'grid_id': 'p1-g0', 'body_row_source': 'pdf_vector_grid'},
               'cells': [{'grid_cell_id': c['grid_cell_id'], 'text': 'Name'} for c in self.grid['header_cells']]}
        before = copy.deepcopy(raw)
        value, _ = assemble_response(q, raw, 'table_facts', grid_hints=[self.grid])
        self.assertEqual(value['answer']['row_count'], 5)
        self.assertEqual(value['full_table_check']['row_breakdown'], {'header': 2, 'body': 2, 'notes': 1})
        self.assertEqual(raw, before)
        for changed in [{**raw, 'table_extent': {**raw['table_extent'], 'col_count': 3}},
                        {**raw, 'cells': raw['cells'][:-1]},
                        {**raw, 'cells': raw['cells'][:-1]+[raw['cells'][0]]}]:
            with self.assertRaises(PipelineError):
                assemble_response(q, changed, 'table_facts', grid_hints=[self.grid])
        with self.assertRaises(PipelineError):
            assemble_response(q, raw, 'table_facts')

    def test_literal_field_conflicts_do_not_generate_substitute_text(self):
        q = Question('s', 's.pdf', 'extract', '从钛合金表提取名义化学成分', answer_format='string')
        with self.assertRaisesRegex(PipelineError, 'Ti元素'):
            validate_field_semantics(q, {'answer': 'Tr-2Al'}, [])
        validate_field_semantics(q, {'answer': 'Ti-2Al'}, [])
        validate_field_semantics(q, {'answer': 'Ti2Al'}, [])
        validate_field_semantics(q, {'answer': '工业纯钛'}, [])
        q = Question('s', 's.pdf', 'extract', '第1页竖排表名', answer_format='string')
        with self.assertRaisesRegex(PipelineError, '表名/题注'):
            validate_field_semantics(q, {'answer': 'GB/T 1234'}, [])
        validate_field_semantics(q, {'answer': '表3（续）', 'grounding': {'table': '表3 原名'}}, [])

    def test_nested_cells_and_explicit_multiline_text_have_unique_geometry(self):
        q = Question('s','s.pdf','structure','恢复表头行结构')
        texts = [{'grid_cell_id':'h0','text':'A'},{'grid_cell_id':'h1','text':'B'},
                 {'grid_cell_id':'h2','text':'Group\nLeft Right'}]
        raw = {'table_extent':{'page':1,'grid_id':'p1-g0','body_row_source':'pdf_vector_grid','cells':texts}}
        before = copy.deepcopy(raw)
        value,_ = assemble_response(q,raw,'table_facts',grid_hints=[self.grid])
        self.assertEqual([c['text'] for c in value['answer']['cells']],['A','B','Group','Left','Right'])
        self.assertEqual(raw,before)
        self.assertTrue(value['pdf_grid_check']['cells_unwrapped'])
        self.assertEqual(len(value['pdf_grid_check']['text_expansions']),1)
        with self.assertRaises(PipelineError):
            assemble_response(q,{**raw,'cells':texts},'table_facts',grid_hints=[self.grid])

    def test_percent_extreme_checks_visible_column_beyond_model_records(self):
        q = Question('s', 's.pdf', 'thinking', '找出RX最高的名称', answer_format='string')
        page = {'pdf_words': [{'text': 'R', 'bbox': [98, 0, 100, 5]}, {'text': 'X', 'bbox': [101, 0, 103, 5]}]
                + [{'text': v, 'bbox': [95, y, 105, y+5]} for y, v in [(20, '4%'), (30, '9%'), (40, '6%'), (50, '3%')]]}
        raw = {'records': [{'column': 'RX', 'value': '4%'}], 'verifications': [{'op': 'argmax'}]}
        with self.assertRaisesRegex(PipelineError, '遗漏'):
            validate_field_semantics(q, raw, [page])
        page['pdf_words'].append({'text':'Parent','bbox':[95,-15,105,-10]})
        with self.assertRaisesRegex(PipelineError, '遗漏'):
            validate_field_semantics(q, raw, [page])
        raw['records'].append({'column': 'RX', 'value': '9%'})
        validate_field_semantics(q, raw, [page])
