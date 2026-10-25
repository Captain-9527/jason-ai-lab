"""自建带正文、侧分组列的PDF；不使用比赛文件或模型请求。"""
import tempfile
import unittest
from pathlib import Path

import pymupdf

from table_qa.config import load_config, validate_config
from table_qa.infrastructure.tracing import Trace
from table_qa.processing.document import prepare_document
from table_qa.processing.views import enrich_pages, question_views, upright_ruled_view, table_number_target
from table_qa.schemas import Question, PipelineError
from table_qa.validation.structure_scope import validate_structure_scope
from table_qa.solving.direct import solve_direct
from table_qa.validation.grounding import local_checks


class UprightPdfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = load_config()
        self.cfg['paths']['cache'] = str(self.root/'cache')
        self.cfg['processing'].update(dpi=72, preprocess=False, pdf_auto_orient=False)
        source = self.root/'sample.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=400)
            page.insert_text((50, 75), 'Table 9 | Synthetic comparison')
            for y in (100, 140, 210, 280):
                page.draw_line((50, y), (550, y))
            for x, text in [(180, 'Metric Name'), (340, 'Model Alpha'), (480, 'Model Beta')]:
                page.insert_text((x, 125), text)
            page.insert_text((55, 175), 'Group')
            for i, y in enumerate((160, 185, 215, 240, 265)):
                page.insert_text((180, y), f'Item name {chr(65+i)}')
                page.insert_text((350, y), str(i+2))
                page.insert_text((490, y), str(i+3))
            page.insert_text((50, 325), 'A paragraph outside the table is not a data row.')
            doc.save(source)
        self.trace = Trace(self.root/'trace.jsonl')
        self.pages = enrich_pages(source, prepare_document(source, self.cfg, self.trace),
                                  self.cfg, self.root, self.trace)
        self.q = Question('s', source.name, 'structure', '恢复 Table 9 的表头行结构', route='structure')

    def test_only_captioned_ruled_table_is_cropped_and_group_column_is_kept(self):
        page = question_views(self.q, self.pages, self.cfg, self.root, self.trace)[0]
        self.assertTrue(page['table_focused'])
        self.assertEqual(page['selection_basis'], 'unique_captioned_ruled_table')
        self.assertEqual(page['view_bbox'], [44, 94, 556, 286])
        self.assertEqual(page['pdf_layout_hint']['body_text_row_count'], 5)
        self.assertTrue(page['pdf_layout_hint']['leading_group_column_visible'])
        self.assertIn('Table 9', page['outside_table_context']['above'])

    def test_wrong_caption_or_missing_caption_does_not_crop(self):
        q = Question('s', 's.pdf', 'structure', '恢复 Table 8 的表头行结构')
        self.assertIsNone(upright_ruled_view(q, self.pages[0], self.root, self.trace))
        page = {**self.pages[0], 'pdf_words': [w for w in self.pages[0]['pdf_words'] if w['text'] != 'Table']}
        self.assertIsNone(upright_ruled_view(self.q, page, self.root, self.trace))

    def test_inline_table_reference_is_not_a_second_caption(self):
        words = [{'text':'See','bbox':[50,320,70,331]},
                 {'text':'Table','bbox':[80,320,108,331]},
                 {'text':'9','bbox':[112,320,118,331]},
                 {'text':'for details','bbox':[125,320,180,331]}]
        page = {**self.pages[0],'pdf_words':self.pages[0]['pdf_words']+words}
        self.assertIsNotNone(upright_ruled_view(self.q,page,self.root,self.trace))

    def test_numeric_localization_uses_full_page_geometry_without_second_offset(self):
        q = Question('s', 's.pdf', 'extract', '从 Table 9 提取 Item name C 的 Model Beta 对应的值',
                     answer_format='number', route='lookup')
        views = question_views(q, self.pages, self.cfg, self.root, self.trace)
        raw = {'answer': '5', 'evidence': [],
               'grounding': {'table': 'Table 9', 'rows': 'Item name C', 'columns': 'Model Beta', 'scope': '单格'}}
        class Model:
            def ask(self, *args, **kwargs): return raw
        candidate = solve_direct(q, views, Model(), cfg=self.cfg)
        self.assertEqual(candidate['pdf_localization']['status'], 'supported')
        self.assertEqual(candidate['pdf_localization']['source'], 'pdf_ruled_grid')
        self.assertAlmostEqual(candidate['evidence'][0]['bbox'][0], 490, places=2)
        self.assertFalse(local_checks(q, candidate, self.pages, self.cfg)[0])

    def test_layout_contract_keeps_conditional_labels_explicit(self):
        views = question_views(self.q, self.pages, self.cfg, self.root, self.trace)
        test = self
        class Model:
            def ask(self, stage, payload, *args, **kwargs):
                extent = payload['response_contract']['table_extent']
                test.assertIn('model时必须', extent['data_row_labels'])
                test.assertIn('pdf_layout_rows', extent['body_row_source'])
                test.assertEqual(payload['table_extent_template']['body_row_source'], 'pdf_layout_rows')
                return {'table_extent': {'page': 1, 'col_count': 4, 'header_rows': 1,
                                        'note_rows': 0, 'body_row_source': 'pdf_layout_rows'},
                        'cells': [{'text': text, 'row': 0, 'col': col, 'rowspan': 1, 'colspan': 1}
                                  for col, text in enumerate(['', 'Metric Name', 'Model Alpha', 'Model Beta'])],
                        'evidence': [{'page': 1, 'bbox_format': 'pixel',
                                      'image_size': [views[0]['width'], views[0]['height']], 'bbox': [0, 0, 500, 50]}],
                        'grounding': {'table': 'Table 9', 'rows': '表头', 'columns': '全部', 'scope': '表头'}}
        candidate = solve_direct(self.q, views, Model(), cfg=self.cfg)
        self.assertEqual(candidate['answer']['row_count'], 6)

    def test_inner_table_caption_rejects_combined_frame(self):
        page = {**self.pages[0], 'pdf_words': self.pages[0]['pdf_words'] +
                [{'text': 'Table', 'bbox': [60, 180, 90, 195]}]}
        self.assertIsNone(upright_ruled_view(self.q, page, self.root, self.trace))

    def test_range_hint_does_not_override_explicit_single_table(self):
        q = Question('s', 's.pdf', 'extract', '从Table 8-10评估中提取：Table 10的值',
                     table_hint='Table 8-10')
        self.assertEqual(table_number_target(q), ('10', False))
        q = Question('s', 's.pdf', 'extract', '对比 Table 9 和 Table 10')
        self.assertEqual(table_number_target(q), (None, True))

    def test_caption_separates_same_width_stacked_tables(self):
        page = self.pages[0]
        extra_words = [{'text': w['text'], 'bbox': [w['bbox'][0], w['bbox'][1]+300,
                         w['bbox'][2], w['bbox'][3]+300]} for w in page['pdf_words']]
        # 第二份独立题注/框架必须按编号选取，不能把两表当一个框。
        for word in extra_words:
            if word['text'] == '9':
                word['text'] = '10'
        combined = {**page, 'height': 700, 'pdf_words': page['pdf_words']+extra_words,
                    'pdf_horizontal_rules': page['pdf_horizontal_rules']+
                    [[r[0], r[1]+300, r[2], r[3]+300] for r in page['pdf_horizontal_rules']]}
        first = upright_ruled_view(self.q, combined, self.root, self.trace)
        self.assertEqual(first['view_bbox'][1:4:2], [94, 286])
        self.assertEqual(first['pdf_layout_hint']['body_text_row_count'], 5)
        no_target = Question('s', 's.pdf', 'structure', '恢复表头行结构')
        self.assertIsNone(upright_ruled_view(no_target, combined, self.root, self.trace))

    def test_nested_subtotal_rules_are_not_a_second_table(self):
        page = {**self.pages[0], 'pdf_horizontal_rules': self.pages[0]['pdf_horizontal_rules']+
                [[180, y, 550, y] for y in (175, 225, 250)]}
        view = upright_ruled_view(self.q, page, self.root, self.trace)
        self.assertEqual(view['pdf_layout_hint']['body_text_row_count'], 5)
        self.assertEqual(view['view_bbox'], [44, 94, 556, 286])

    def test_blank_last_column_does_not_remove_a_data_row(self):
        page={**self.pages[0],'pdf_words':[w for w in self.pages[0]['pdf_words']
                    if not(w['bbox'][0]>480 and 200<w['bbox'][1]<220)]}
        view=upright_ruled_view(self.q,page,self.root,self.trace)
        self.assertEqual(view['pdf_layout_hint']['body_text_row_count'],5)

    def test_two_row_indented_table_keeps_left_aligned_caption(self):
        page = self.pages[0]
        # 去除侧列、后三行及中间规则；题注位于窄表左边界之外。
        words = [w for w in page['pdf_words'] if w['bbox'][0] >= 170 or w['bbox'][1] < 90]
        words = [w for w in words if w['bbox'][1] < 200]
        narrow = {**page, 'pdf_words': words,
                  'pdf_horizontal_rules': [[170, y, 520, y] for y in (100, 140, 200)]}
        view = upright_ruled_view(self.q, narrow, self.root, self.trace)
        self.assertEqual(view['pdf_layout_hint']['body_text_row_count'], 2)

    def test_switch_disabled_and_metadata_keep_original_page(self):
        self.cfg['run']['focus_ruled_pdf_tables'] = False
        self.assertNotIn('view_to_page', question_views(self.q, self.pages, self.cfg, self.root, self.trace)[0])
        self.cfg['run']['focus_ruled_pdf_tables'] = 'yes'
        with self.assertRaises(ValueError):
            validate_config(self.cfg)

    def test_complete_header_must_cover_blank_corner_and_all_levels(self):
        cells = [{'text': '', 'row': 0, 'col': 0, 'rowspan': 2, 'colspan': 1},
                 {'text': 'H', 'row': 0, 'col': 1, 'rowspan': 1, 'colspan': 2},
                 {'text': 'A', 'row': 1, 'col': 1, 'rowspan': 1, 'colspan': 1},
                 {'text': 'B', 'row': 1, 'col': 2, 'rowspan': 1, 'colspan': 1}]
        answer = {'row_count': 3, 'col_count': 3, 'cells': cells}
        check = {'row_breakdown': {'header': 2, 'body': 1, 'notes': 0}}
        validate_structure_scope(self.q, answer, check)
        with self.assertRaisesRegex(PipelineError, '未覆盖'):
            validate_structure_scope(self.q, {**answer, 'cells': cells[1:]}, check)
        subset = Question('s', 's.pdf', 'structure', '恢复指定列的表头')
        validate_structure_scope(subset, {**answer, 'cells': cells[1:]}, check)
