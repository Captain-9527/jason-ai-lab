"""合成三线表定位回归：无比赛题号/答案，不请求模型。"""
import copy
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

import numpy as np
from PIL import Image, ImageDraw

from table_qa.config import load_config, validate_config
from table_qa.schemas import Question
from table_qa.processing.views import transform_box
from table_qa.solving.direct import solve_direct
from table_qa.validation.grounding import local_checks
from table_qa.validation.pdf_localization import localize_pdf_value
from table_qa.validation.pdf_names import pdf_row_name_hints, validate_pdf_name_labels
from table_qa.schemas import PipelineError


class PdfLocalizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        image = Image.new('RGB', (600, 240), 'white')
        draw = ImageDraw.Draw(image)
        for y in (5, 75, 210):
            draw.line((0, y, 599, y), fill='black', width=2)
        path = Path(self.tmp.name)/'synthetic.png'
        image.save(path)
        self.words = []
        def word(text, x, y, width):
            self.words.append({'text': text, 'bbox': [x, y, x+width, y+10]})
        for text, y in [('Mean', 20), ('annual', 32), ('rainfall', 44), ('(mm)', 56)]:
            word(text, 200, y, 45)
        word('Other', 400, 20, 40)
        for y, name, value, other in [(100, 'Delta', '7', '3'), (140, 'Echo', '9', '7')]:
            word(name, 10, y, 30)
            word('(X)', 45, y, 20)
            word(value, 200, y, 15)
            word(other, 400, y, 15)
        self.page = {'page': 1, 'width': 600, 'height': 240, 'image': str(path),
                     'view_to_page': np.eye(3).tolist(), 'table_focused': True, 'pdf_words': self.words}
        self.q = Question('synthetic', 'synthetic.pdf', 'extract',
                          '提取 Delta (X) 的 Mean annual rainfall (mm) 对应的值',
                          answer_format='number', route='lookup')
        self.candidate = {'answer': '7', 'grounding': {'table': 'T', 'rows': 'Delta (X)',
                          'columns': 'Mean annual rainfall (mm)', 'scope': '单格'}}
        self.cfg = load_config()

    def test_unique_row_column_intersection_not_nearest_value(self):
        before = copy.deepcopy(self.candidate)
        result = localize_pdf_value(self.q, self.candidate, [self.page])
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['evidence'], [{'page': 1, 'bbox': [200, 100, 215, 110]}])
        self.assertEqual(result['anchors']['column']['bbox'], [200, 20, 245, 66])
        self.assertEqual(self.candidate, before)

    def test_wrong_answer_is_never_replaced_by_intersection_value(self):
        self.words[7]['text'] = '99'  # 目标格原值；另一行/列仍有7。
        result = localize_pdf_value(self.q, self.candidate, [self.page])
        self.assertEqual(result['status'], 'value_mismatch')
        self.assertEqual(result['observed_texts'], ['99'])
        self.assertEqual(self.candidate['answer'], '7')
        self.assertNotIn('evidence', result)

    def test_repeated_row_names_are_ambiguous(self):
        self.words[9]['text'] = 'Delta'
        result = localize_pdf_value(self.q, self.candidate, [self.page])
        self.assertEqual(result['status'], 'skipped')

    def test_repeated_column_names_are_ambiguous(self):
        self.words[4]['text'] = 'Mean annual rainfall (mm)'
        self.assertEqual(localize_pdf_value(self.q, self.candidate, [self.page])['status'], 'skipped')

    def test_multiple_words_in_value_cell_are_not_concatenated(self):
        self.words.append({'text': 'unit', 'bbox': [220, 100, 240, 110]})
        self.assertEqual(localize_pdf_value(self.q, self.candidate, [self.page])['status'], 'unsupported_value')

    def test_labels_must_be_literal_in_question(self):
        q = replace(self.q, question='提取 Echo (X) 的 Mean annual rainfall (mm) 对应的值')
        self.assertEqual(localize_pdf_value(q, self.candidate, [self.page])['status'], 'skipped')

    def test_decorated_model_labels_use_unique_question_and_pdf_labels(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['grounding']['rows'] += ' (first data row)'
        candidate['grounding']['columns'] += ' (second column)'
        self.assertEqual(localize_pdf_value(self.q, candidate, [self.page])['status'], 'supported')
        ambiguous = replace(self.q, question=self.q.question+'，并提到 Echo (X)')
        self.assertEqual(localize_pdf_value(ambiguous, candidate, [self.page])['status'], 'skipped')

    def test_solve_preserves_answer_and_audits_decorated_model_labels(self):
        raw = copy.deepcopy(self.candidate)
        raw['grounding']['rows'] += ' (first data row)'
        before = copy.deepcopy(raw)
        class Model:
            def ask(self, *args, **kwargs): return raw
        candidate = solve_direct(self.q, [self.page], Model(), cfg=self.cfg)
        self.assertEqual(candidate['answer'], raw['answer'])
        self.assertEqual(candidate['grounding']['rows'], 'Delta (X)')
        self.assertEqual(candidate['pdf_localization']['model_grounding'], raw['grounding'])
        self.assertEqual(local_checks(self.q, candidate, [self.page], self.cfg)[0], [])
        self.assertEqual(raw, before)

    def test_no_text_or_unfocused_or_multiple_pages_abstain(self):
        for pages in [[{**self.page, 'pdf_words': []}], [{**self.page, 'table_focused': False}],
                      [self.page, {**self.page, 'page': 2}]]:
            self.assertEqual(localize_pdf_value(self.q, self.candidate, pages)['status'], 'skipped')

    def test_structures_calculations_and_strings_are_not_relocalized(self):
        for q in [replace(self.q, question_type='structure'), replace(self.q, route='calculation'),
                  replace(self.q, answer_format='string')]:
            self.assertEqual(localize_pdf_value(q, self.candidate, [self.page])['status'], 'not_applicable')

    def test_font_encoding_or_multitoken_value_is_not_guessed(self):
        self.words[7]['text'] = '!7'
        result = localize_pdf_value(self.q, self.candidate, [self.page])
        self.assertEqual(result['status'], 'unsupported_value')

    def test_rotated_view_maps_evidence_and_anchors_once_and_preserves_raw(self):
        matrix = [[0, 1, 30], [-1, 0, 700], [0, 0, 1]]
        page = {**self.page, 'view_to_page': matrix,
                'pdf_words': [{'text': w['text'], 'bbox': transform_box(w['bbox'], matrix)} for w in self.words]}
        raw = {**self.candidate, 'evidence': [{'page': 1, 'bbox': [0, 0, 99999, 99999]}]}
        before = copy.deepcopy(raw)
        class Model:
            def ask(self, *args, **kwargs): return raw
        candidate = solve_direct(self.q, [page], Model(), cfg=self.cfg)
        self.assertEqual(candidate['pdf_localization']['status'], 'supported')
        self.assertEqual(candidate['evidence'][0]['bbox'], [130, 485, 140, 500])
        self.assertFalse(candidate['warnings'])
        self.assertTrue(candidate['pdf_localization']['model_warnings'])
        self.assertEqual(local_checks(self.q, candidate, [page], self.cfg)[0], [])
        self.assertEqual(raw, before)

    def test_feature_switch_and_config_validation(self):
        self.cfg['run']['localize_pdf_evidence'] = False
        class Model:
            def ask(inner, *args, **kwargs): return {**self.candidate, 'evidence': []}
        candidate = solve_direct(self.q, [self.page], Model(), cfg=self.cfg)
        self.assertNotIn('pdf_localization', candidate)
        self.assertTrue(candidate['warnings'])
        self.cfg['run']['localize_pdf_evidence'] = 'yes'
        with self.assertRaises(ValueError):
            validate_config(self.cfg)

    def test_name_labels_are_literal_pdf_names_not_fuzzy_corrections(self):
        hints = pdf_row_name_hints([self.page])
        self.assertEqual(hints[0]['row_labels'], ['Delta (X)', 'Echo (X)'])
        plan = {'op': 'argmax', 'labels': ['Delta (X)', 'Echo (X)']}
        self.assertEqual(validate_pdf_name_labels([plan], hints)['checked_label_count'], 2)
        with self.assertRaisesRegex(PipelineError, '漏字/改拼写'):
            validate_pdf_name_labels([{**plan, 'labels': ['Delta (X)', 'Eho (X)']}], hints)
        self.assertIsNone(validate_pdf_name_labels([plan], []))
        self.assertFalse(pdf_row_name_hints([{**self.page, 'table_focused': False}]))

    def test_filter_receives_source_names_and_checks_every_label(self):
        q = replace(self.q, question_type='thinking', question='找出最高值对应的名称',
                    answer_format='string', route='filter')
        raw = {'records': [{'value': value, 'label': name, 'row': name, 'column': 'Other', 'level': '数据'}
                           for value, name in [('3', 'Delta (X)'), ('7', 'Echo (X)')]],
               'operations': [{'op': 'argmax', 'separator': ', '}],
               'grounding': {'table': 'T', 'rows': '全部数据行', 'columns': 'Other', 'scope': '完整'},
               'scope_check': {'complete': True, 'excluded': []}}
        test = self
        class Model:
            def ask(self, stage, payload, *args, **kwargs):
                test.assertEqual(payload['pdf_row_name_hints'][0]['row_labels'], ['Delta (X)', 'Echo (X)'])
                return raw
        candidate = solve_direct(q, [self.page], Model(), cfg=self.cfg)
        self.assertEqual(candidate['answer'], 'Echo (X)')
        self.assertEqual(candidate['pdf_name_check']['status'], 'literal_names_supported')
        raw['records'][1]['label'] = 'Eho (X)'
        with self.assertRaisesRegex(PipelineError, 'PDF完整行名不一致'):
            solve_direct(q, [self.page], Model(), cfg=self.cfg)

    def test_structure_cannot_drop_last_pdf_header_column(self):
        q=replace(self.q,question_type='structure',question='恢复表头行结构',answer_format='json',route='structure')
        raw={'answer':{'row_count':3,'col_count':2,'cells':[
            {'text':'','row':0,'col':0,'rowspan':1,'colspan':1},
            {'text':'Mean annual rainfall (mm)','row':0,'col':1,'rowspan':1,'colspan':1}]},
             'full_table_check':{'scope':'full_table','row_count':3,'col_count':2,'page':1,
                                 'basis':'synthetic','row_breakdown':{'header':1,'body':2,'notes':0}}}
        class Model:
            def ask(self,*args,**kwargs):return raw
        with self.assertRaisesRegex(PipelineError,'遗漏最右列'):
            solve_direct(q,[self.page],Model(),cfg=self.cfg)
