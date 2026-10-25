"""新默认单路线：调用计数、证据内容、局部转正、范围与独立导出实验。"""
import json
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import pymupdf

from tests.integration import test_pipeline as fixtures
from table_qa.application.runner import execute, export_run
from table_qa.application.routing import classify
from table_qa.config import load_config
from table_qa.infrastructure.model_client import prompt_data, ModelClient
from table_qa.infrastructure.storage import read_results
from table_qa.infrastructure.tracing import Trace
from table_qa.processing.document import prepare_document
from table_qa.processing.views import enrich_pages, question_views, transform_box
from table_qa.schemas import Question, PipelineError
from table_qa.solving.direct import solve_direct
from table_qa.validation.answer import format_answer
from table_qa.validation.consistency import check_document_consistency
from table_qa.validation.grounding import local_checks


class SingleModel(fixtures.FakeModel):
    def ask(self, stage, payload, images=None, vision=True, fresh=False):
        self.calls.append((stage, fresh))
        if stage in {'review', 'structured'}:
            raise AssertionError('自动单路线不应调用另一解题路线或复核')
        if stage != 'direct':
            raise AssertionError(stage)
        kind = payload['question']['question_type']
        answer = '30' if kind == 'thinking' else '10'
        raw = {'answer': answer, 'evidence': [{'page': 1, 'bbox': [0, 0, 200, 100]}],
               'grounding': {'table': '合成表', 'rows': 'A', 'columns': '数值', 'scope': '目标单元格'}}
        if kind == 'thinking':
            raw.update(verification={'op': 'sum', 'values': ['10', '20']},
                       scope_check={'complete': True, 'excluded': ['表头']})
        if kind == 'structure':
            raw['answer'] = {'row_count': 2, 'col_count': 2, 'cells': [
                {'text': 'A', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]}
            raw['full_table_check'] = {'scope': 'full_table', 'row_count': 2, 'col_count': 2,
                                      'page': 1, 'basis': 'A/B两行，无独立表头',
                                      'row_breakdown': {'header': 0, 'body': 2, 'notes': 0}}
        return raw


class SinglePassTest(unittest.TestCase):
    setUp = fixtures.IntegrationTest.setUp
    tearDown = fixtures.IntegrationTest.tearDown

    def single(self):
        self.cfg['run'].update(single_pass=True, review=False, strategy='auto')
        return self.cfg

    def test_all_types_use_one_solver_and_zero_reviews(self):
        cfg = self.single()
        events = []
        report = execute(cfg, model_factory=SingleModel,
                         progress=lambda done, total, record: events.append((done, total, record)))
        self.assertEqual([(done, total) for done, total, _ in events], [(0, 3), (1, 3), (2, 3), (3, 3)])
        self.assertEqual(report['selected_count'], 3)
        self.assertEqual(report['local_states'], {'success': 3})
        self.assertEqual(fixtures.FakeModel.calls, [('direct', False)] * 3)
        self.assertEqual(report['states'], {'success': 3})
        results = read_results(Path(cfg['paths']['outputs']) / report['run_id'])
        self.assertTrue(all(r['validation_level'] == 'program_checked' and not r['review_history'] for r in results.values()))

    def test_compare_is_collapsed_even_if_review_flag_is_true(self):
        cfg = self.single()
        cfg['run'].update(strategy='compare', review=True)
        q = Question('x', 'sample.png', 'structure', '表头', answer_format='json')
        self.assertEqual(classify(q, cfg)['strategy'], 'direct')
        self.assertFalse(classify(q, cfg)['needs_tables'])

    def test_text_enumeration_has_one_call_without_arithmetic_verification(self):
        from table_qa.application.pipeline import answer_question
        cfg = self.single()
        q = Question('synthetic', 'sample.png', 'thinking', '该表的分类有哪些？', answer_format='json_array')
        class Model:
            calls = []
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls.append(stage)
                self.assertEqual(payload['question']['route'], 'enumeration')
                return {'answer': ['主类A', '其中：子类A'],
                        'evidence': [{'page': 1, 'bbox': [0, 0, 200, 100]}],
                        'grounding': {'table': 'T', 'rows': '主类A/子类A', 'columns': '类别', 'scope': '含子类、不含合计'},
                        'items': [{'value': name, 'row': name, 'column': '类别', 'level': level}
                                  for name, level in [('主类A', '分类'), ('其中：子类A', '子类')]],
                        'scope_check': {'complete': True, 'excluded': ['合计']}}
        model = Model()
        pages = [{'page': 1, 'width': 200, 'height': 100, 'image': 'unused'}]
        result = answer_question(q, pages, None, model, cfg, Trace(self.root / 'enum.trace'), self.root)
        self.assertEqual(model.calls, ['direct'])
        self.assertEqual(result['status'], 'success')

    def test_nested_plan_is_protocol_error_and_can_be_repaired_without_attribute_error(self):
        from table_qa.application.pipeline import answer_question
        cfg = self.single()
        q = Question('synthetic', 'sample.png', 'thinking', '计算两项之和', answer_format='number')
        class Model:
            calls = []
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls.append(stage)
                plan = {'op': 'sum', 'values': ['2', '3']}
                if stage == 'repair':
                    self.assertIn('复算计划必须', str(payload['previous']['issues']))
                return {'answer': '5', 'verification': [[plan]] if stage == 'direct' else [plan],
                        'evidence': [{'page': 1, 'bbox': [0, 0, 200, 100]}],
                        'grounding': {'table': 'T', 'rows': 'A/B', 'columns': '值', 'scope': '两项'},
                        'scope_check': {'complete': True, 'excluded': []}}
        model = Model()
        pages = [{'page': 1, 'width': 200, 'height': 100, 'image': 'unused'}]
        result = answer_question(q, pages, None, model, cfg, Trace(self.root / 'plan.trace'), self.root)
        self.assertEqual(model.calls, ['direct', 'repair'])
        self.assertEqual(result['status'], 'success')

    def test_header_scope_failure_repairs_cells_not_full_dimensions(self):
        from table_qa.application.pipeline import answer_question
        cfg = self.single()
        q = Question('synthetic', 'sample.png', 'structure', '恢复表头行结构', answer_format='json')
        class Model:
            calls = []
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls.append(stage)
                cells = [{'text': 'H', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]
                if stage == 'direct':
                    cells.append({**cells[0], 'text': 'data', 'row': 1})
                return {'answer': {'row_count': 5, 'col_count': 1, 'cells': cells},
                        'evidence': [{'page': 1, 'bbox': [0, 0, 200, 100]}],
                        'grounding': {'table': 'T', 'rows': '表头', 'columns': 'H', 'scope': '仅表头'},
                        'full_table_check': {'scope': 'full_table', 'page': 1, 'basis': '1表头+4数据',
                                             'row_count': 5, 'col_count': 1,
                                             'row_breakdown': {'header': 1, 'body': 4, 'notes': 0}}}
        model = Model()
        pages = [{'page': 1, 'width': 200, 'height': 100, 'image': 'unused'}]
        result = answer_question(q, pages, None, model, cfg, Trace(self.root / 'scope.trace'), self.root)
        self.assertEqual(model.calls, ['direct', 'repair'])
        self.assertEqual(result['answer']['row_count'], 5)
        self.assertEqual(len(result['answer']['cells']), 1)
        self.assertEqual(result['status'], 'success')

    def test_numeric_evidence_must_contain_value_not_just_be_in_bounds(self):
        cfg = self.single()
        q = Question('x', 'sample.pdf', 'extract', '提取A的数值', answer_format='number', route='lookup')
        pages = [{'page': 1, 'pdf_words': [{'text': '1504', 'bbox': [40, 40, 60, 60]},
                                         {'text': 'A', 'bbox': [0, 40, 10, 60]},
                                         {'text': '雨量', 'bbox': [40, 0, 60, 10]}]}]
        c = {'answer': '1104', 'evidence': [{'page': 1, 'bbox': [0, 0, 10, 10]}],
             'grounding': {'table': 'T', 'rows': 'A', 'columns': '雨量', 'scope': '单格'}}
        issues, _ = local_checks(q, c, pages, cfg)
        self.assertTrue(any('未支持' in i for i in issues))
        c.update(answer='1504', evidence=[{'page': 1, 'bbox': [35, 35, 65, 65]}])
        c['anchors'] = {'row': {'page': 1, 'bbox': [0, 35, 15, 65]},
                        'column': {'page': 1, 'bbox': [35, 0, 65, 15]}}
        self.assertEqual(local_checks(q, c, pages, cfg)[0], [])
        pages[0]['pdf_words'].append({'text': '1134', 'bbox': [40, 70, 60, 90]})
        c.update(answer='1134', evidence=[{'page': 1, 'bbox': [35, 65, 65, 95]}])
        self.assertTrue(any('网格交点' in issue for issue in local_checks(q, c, pages, cfg)[0]))

    def test_list_keeps_duplicate_values_with_distinct_row_provenance(self):
        cfg = self.single()
        q = Question('x', 'sample.png', 'extract', '各分类金额', answer_format='json_array', route='lookup')
        c = {'answer': ['12', '12'], 'evidence': [{'page': 1, 'bbox': [0, 0, 100, 100]}],
             'grounding': {'table': 'T', 'rows': '各分类', 'columns': '金额', 'scope': '含子类，不含合计'},
             'items': [{'value': '12', 'row': '甲', 'column': '金额', 'level': '分类'},
                       {'value': '12', 'row': '其中乙', 'column': '金额', 'level': '子类'}],
             'scope_check': {'complete': True, 'excluded': ['合计']}}
        self.assertEqual(local_checks(q, c, [], cfg)[0], [])
        c['items'].pop()
        self.assertTrue(local_checks(q, c, [], cfg)[0])

    def test_cross_question_conflict_flags_both_without_changing_answers(self):
        qs = [Question(str(i), 'a.pdf', 'extract', '值') for i in (1, 2)]
        fact = {'table': 'T', 'row': 'A', 'column': '雨量', 'value': '1504'}
        results = {'1': {'answer': '1104', 'status': 'success', 'facts': [{**fact, 'value': '1104'}]},
                   '2': {'answer': '2940', 'status': 'success', 'facts': [fact]}}
        conflicts = check_document_consistency(qs, results)
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(all(r['status'] == 'uncertain' for r in results.values()))
        self.assertEqual(results['1']['answer'], '1104')
        self.assertEqual(results['2']['answer'], '2940')
        other = [replace(qs[0], file_name='b.pdf'), qs[1]]
        self.assertEqual(check_document_consistency(other, results), [])

    def test_repair_receives_issues_only_and_no_previous_answer(self):
        cfg = self.single()
        q = Question('x', 'sample.png', 'extract', '值', answer_format='number', route='lookup')
        class Model:
            def ask(self, stage, payload, *args, **kwargs):
                self.payload = payload
                return {'answer': '10', 'evidence': [{'page': 1, 'bbox': [0, 0, 200, 100]}]}
        model = Model()
        solve_direct(q, [{'page': 1, 'width': 200, 'height': 100, 'image': 'unused'}], model,
                     repair={'issues': ['无效类型'], 'candidates': {'direct': {'answer': '999'}},
                             'review': {'answer': '888', 'reasoning_content': '秘密推理'}}, cfg=cfg)
        self.assertEqual(model.payload['previous'], {'issues': ['无效类型'], 'requires_calculation': False})
        # 检查旧答案值而非子串，坐标协议norm999合法包含同样的数字。
        self.assertNotIn('"999"', json.dumps(model.payload))

    def test_prompt_data_removes_reasoning_recursively(self):
        raw = {'answer': 3, 'reasoning_content': 'long', 'nested': [{'reasoning': 'long', 'value': 'x'}],
               'review_history': [{'answer': 999}]}
        self.assertEqual(prompt_data(raw), {'answer': 3, 'nested': [{'value': 'x'}]})
        self.assertIn('reasoning_content', raw)

    def test_client_guard_blocks_review_before_sdk_or_budget(self):
        client = object.__new__(ModelClient)
        client.cfg = self.single()
        with self.assertRaisesRegex(PipelineError, '禁止模型复核'):
            client.ask('review', {})

    def test_structure_repair_keeps_explicit_grid_contract_and_rejects_pixel_cells(self):
        from table_qa.application.pipeline import answer_question
        cfg = self.single()
        cfg['run']['program_assembly'] = False  # 显式验证旧协议兼容路径。
        q = Question('synthetic', 'sample.png', 'structure', '恢复表头', answer_format='json')
        pages = [{'page': 1, 'width': 200, 'height': 100, 'image': str(self.root / 'files/sample.png')}]
        class BadStructure:
            def __init__(self):
                self.payloads = []
            def ask(self, stage, payload, *args, **kwargs):
                self.payloads.append((stage, payload))
                if stage == 'direct':
                    return {'answer': {'table': {'row_count': 3, 'col_count': 2, 'cells': []}}}
                return {'answer': {'row_count': 1, 'col_count': 2,
                                   'cells': [{'text': 'A', 'bbox_norm': [0, 0, 100, 100]}]}}
        model = BadStructure()
        record = answer_question(q, pages, None, model, cfg, Trace(self.root / 'contract.trace'), self.root)
        self.assertIsNone(record['answer'])
        self.assertEqual(record['candidates'], {})
        self.assertEqual([s for s, _ in model.payloads], ['direct', 'repair'])
        contract = model.payloads[0][1]['response_contract']
        self.assertEqual(contract, model.payloads[1][1]['response_contract'])
        self.assertEqual(set(contract['answer']), {'row_count', 'col_count', 'cells'})
        self.assertIn('禁止answer.table', model.payloads[1][1]['previous']['issues'][0])
        self.assertTrue(any('像素bbox不能替代' in w for w in record['warnings']))

    def test_structure_internal_dimensions_are_protocol_errors(self):
        cfg = self.single()
        q = Question('s', 'sample.png', 'structure', '恢复表头', answer_format='json')
        raw = {'answer': {'row_count': 3, 'col_count': 2, 'cells': [
            {'text': 'A', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]},
            'full_table_check': {'row_count': 15, 'col_count': 2, 'page': 1,
                                 'row_breakdown': {'header': 1, 'body': 10, 'notes': 1}}}
        class Model:
            def ask(self, *args, **kwargs):
                return raw
        pages = [{'page': 1, 'width': 200, 'height': 100, 'image': 'unused'}]
        with self.assertRaisesRegex(PipelineError, '自相矛盾'):
            solve_direct(q, pages, Model(), cfg=cfg)
        raw['full_table_check']['row_count'] = 3
        with self.assertRaisesRegex(PipelineError, '自相矛盾'):
            solve_direct(q, pages, Model(), cfg=cfg)

    def test_local_three_rule_hint_excludes_outside_footer_and_handles_wrap(self):
        from PIL import Image, ImageDraw
        from table_qa.processing.grid import simple_grid_hint
        image = Image.new('RGB', (600, 300), 'white')
        draw = ImageDraw.Draw(image)
        for y in (50, 100, 250):
            draw.line((10, y, 590, y), fill='black', width=2)
        path = self.root / 'grid.png'
        image.save(path)
        words = [{'text': 'Value', 'bbox': [150, 65, 210, 85]}] + [
            {'text': name, 'bbox': [12, y, 80, y+20]} for name, y in [('Alpha', 115), ('Beta', 155), ('Gamma', 195)]]
        words += [{'text': 'Footer', 'bbox': [12, 270, 80, 290]}]
        page = {'image': str(path), 'view_to_page': np.eye(3).tolist(), 'pdf_words': words}
        hint = simple_grid_hint(page)
        self.assertEqual(hint['row_count'], 4)
        self.assertEqual(hint['row_breakdown'], {'header': 1, 'body': 3, 'notes': 0})
        self.assertEqual(hint['body_row_labels'], ['Alpha', 'Beta', 'Gamma'])
        self.assertTrue(hint['heuristic'])
        words.append({'text': 'wrapped', 'bbox': [12, 133, 80, 153]})
        self.assertIsNone(simple_grid_hint(page))
        words.pop()
        draw.line((10, 180, 590, 180), fill='black', width=2)
        image.save(path)
        self.assertIsNone(simple_grid_hint(page))

    def test_local_grid_hint_does_not_silently_override_model_dimensions(self):
        from unittest.mock import patch
        cfg = self.single()
        q = Question('s', 'sample.png', 'structure', '恢复表头', answer_format='json')
        raw = {'answer': {'row_count': 5, 'col_count': 2, 'cells': [
            {'text': 'A', 'row': 0, 'col': 0, 'rowspan': 1, 'colspan': 1}]},
            'full_table_check': {'row_count': 5, 'col_count': 2, 'page': 1,
                                 'row_breakdown': {'header': 1, 'body': 4, 'notes': 0}},
            'evidence': [{'page': 1, 'bbox': [0, 0, 200, 100]}]}
        class Model:
            def ask(self, stage, payload, *args, **kwargs):
                self.payload = payload
                return raw
        model = Model()
        pages = [{'page': 1, 'width': 200, 'height': 100, 'image': 'unused'}]
        hint = {'row_count': 4, 'row_breakdown': {'header': 1, 'body': 3, 'notes': 0}}
        with patch('table_qa.solving.direct.simple_grid_hint', return_value=hint):
            result = solve_direct(q, pages, model, cfg=cfg)
        self.assertEqual(result['answer']['row_count'], 5)
        self.assertTrue(any('启发式计数冲突' in w for w in result['warnings']))
        self.assertEqual(model.payload['local_grid_hints'][0]['row_count'], 4)

    def test_mixed_pdf_region_rotation_and_inverse_mapping(self):
        cfg = self.single()
        cfg['processing'].update(dpi=72, preprocess=False)
        pdf = self.root / 'files/mixed.pdf'
        with pymupdf.open() as d:
            p = d.new_page(width=500, height=500)
            for y in range(30, 250, 20):
                p.insert_text((20, y), 'Horizontal body with explanatory text about the document.')
            for x, text in [(70, 'Table Alpha annual rainfall and catchment data'),
                            (85, 'Catchment A rainfall 1504 with reference and details'),
                            (100, 'Catchment B rainfall 1134 with reference and details')]:
                p.insert_text((x, 470), text, rotate=90)
            d.save(pdf)
        trace = Trace(self.root / 'views.trace')
        pages = prepare_document(pdf, cfg, trace)
        enriched = enrich_pages(pdf, pages, cfg, self.root, trace)
        self.assertTrue(enriched[0]['orientation_regions'])
        q = Question('x', 'mixed.pdf', 'extract', 'Alpha annual rainfall Catchment A', route='lookup')
        views = question_views(q, enriched, cfg, self.root, trace)
        self.assertIn('view_to_page', views[0])
        self.assertEqual(len(views), len(pages))
        v = views[0]
        back = transform_box([0, 0, v['width'], v['height']], v['view_to_page'])
        np.testing.assert_allclose(back, v['view_bbox'], atol=1)
        self.assertEqual(question_views(replace(q, route='metadata'), enriched, cfg, self.root, trace), enriched)

    def focused_table_page(self):
        """不依赖比赛文件的混排三线表：左侧放置旋转表，右侧为正文。"""
        from PIL import Image, ImageDraw
        table = Image.new('RGB', (500, 300), 'white')
        draw = ImageDraw.Draw(table)
        for y in (50, 110, 250):
            draw.line((10, y, 490, y), fill='black', width=2)
        words = []
        for y, texts in [(70, ['Label', 'Area', 'Rainfall']), (130, ['Alpha', '10', '1504']),
                         (170, ['Beta', '20', '1134']), (210, ['Gamma', '30', '1436'])]:
            for x, text in zip((20, 180, 350), texts):
                draw.text((x, y), text, fill='black')
                words.append({'text': text, 'bbox': [x, y, x+50, y+14]})
        for y, text in [(15, 'Table title'), (275, 'Outside footnote')]:
            draw.text((20, y), text, fill='black')
            words.append({'text': text, 'bbox': [20, y, 120, y+14]})
        # table逆时针放在原页，局部视图顺时针转正。
        page = Image.new('RGB', (700, 600), 'white')
        page.paste(table.transpose(Image.Transpose.ROTATE_90), (20, 50))
        ImageDraw.Draw(page).text((400, 80), 'Unrelated body paragraph', fill='black')
        path = self.root / 'mixed-table.png'
        page.save(path)
        matrix = np.array([[0, 1, 20], [-1, 0, 550], [0, 0, 1]])
        return {'page': 1, 'image': str(path), 'width': 700, 'height': 600,
                'pdf_words': [{**v, 'bbox': transform_box(v['bbox'], matrix)} for v in words],
                'orientation_regions': [{'bbox': [20, 50, 320, 550], 'degrees': 90,
                                         'text': 'Alpha Beta Gamma Area Rainfall Table title Outside footnote'}]}

    def test_generic_structure_focuses_unique_ruled_mixed_region(self):
        cfg = self.single()
        page = self.focused_table_page()
        q = Question('s', 'mixed.pdf', 'structure', '恢复表头行结构', route='structure')
        trace = Trace(self.root / 'focus.trace')
        view = question_views(q, [page], cfg, self.root, trace)[0]
        self.assertTrue(view['table_focused'])
        self.assertEqual(view['selection_basis'], 'unique_ruled_region')
        self.assertLess(view['height'], 230)
        self.assertGreater(view['width'], 470)
        context = view['outside_table_context']
        self.assertIn('Table title', context['above'])
        self.assertIn('Outside footnote', context['below'])
        self.assertNotIn('Unrelated', str(context))
        back = transform_box([0, 0, view['width'], view['height']], view['view_to_page'])
        np.testing.assert_allclose(back, view['view_bbox'], atol=1e-6)
        # 裁剪/旋转后目标数字的回映仍必须落回原页同一个框。
        target = page['pdf_words'][5]['bbox']
        local = transform_box(target, np.linalg.inv(view['view_to_page']))
        np.testing.assert_allclose(transform_box(local, view['view_to_page']), target, atol=1e-6)

    def test_focus_is_conservative_for_ambiguous_tables_and_context_questions(self):
        cfg = self.single()
        page = self.focused_table_page()
        q = Question('s', 'mixed.pdf', 'structure', '恢复表头行结构', route='structure')
        trace = Trace(self.root / 'fallback.trace')
        ambiguous = {**page, 'orientation_regions': page['orientation_regions'] * 2}
        self.assertEqual(question_views(q, [ambiguous], cfg, self.root, trace), [ambiguous])
        # 没有题意匹配的查找题不能凭唯一侧放表猜目标，正文中仍可能有其他目标。
        self.assertEqual(question_views(replace(q, question_type='extract', route='lookup'), [page], cfg, self.root, trace), [page])
        cfg['processing']['focus_table_content'] = False
        self.assertEqual(question_views(q, [page], cfg, self.root, trace), [page])
        matched = replace(q, question='Alpha Rainfall 表头')
        view = question_views(matched, [page], cfg, self.root, trace)[0]
        self.assertIn('view_to_page', view)
        self.assertFalse(view['table_focused'])
        cfg['processing']['focus_table_content'] = True
        for route in ('metadata', 'visual'):
            self.assertEqual(question_views(replace(matched, route=route), [page], cfg, self.root, trace), [page])
        self.assertEqual(question_views(replace(matched, question='Alpha Rainfall 恢复所有表'), [page], cfg, self.root, trace), [page])
        view = question_views(replace(matched, question='Alpha Rainfall 脚注说明'), [page], cfg, self.root, trace)[0]
        self.assertFalse(view['table_focused'])
        self.assertEqual(question_views(replace(q, question='解释脚注'), [page], cfg, self.root, trace), [page])

    def test_extra_rule_does_not_get_mistaken_for_complete_three_rule_table(self):
        from PIL import Image, ImageDraw
        page = self.focused_table_page()
        cfg = self.single()
        q = Question('s', 'mixed.pdf', 'structure', '恢复表头', route='structure')
        # 原页再添一条与表线同方向的长线，通用题应回退，不裁掉潜在第二张表。
        with Image.open(page['image']) as im:
            ImageDraw.Draw(im).line((220, 60, 220, 540), fill='black', width=2)
            im.save(page['image'])
        self.assertEqual(question_views(q, [page], cfg, self.root, Trace(self.root / 'extra.trace')), [page])

    def test_default_disables_thinking_and_format_profiles_are_lossless(self):
        cfg = load_config()
        self.assertTrue(cfg['run']['single_pass'])
        self.assertTrue(all(o.get('enable_thinking') is False for o in cfg['model']['stages'].values()))
        q = Question('x', 'a', 'extract', '金额列表', answer_format='json_array')
        text = format_answer(q, ['12345678901234567890.50', '', 'A003'], {'array_numbers': 'number'})
        self.assertEqual(text, '[12345678901234567890.50,"","A003"]')
        q = replace(q, question='主要语言', answer_format='string')
        self.assertEqual(format_answer(q, 'English', {'language_names': 'zh'}), '英文')
        self.assertEqual(format_answer(replace(q, question='原始标题'), 'English', {'language_names': 'zh'}), 'English')

    def test_export_profile_does_not_overwrite_submission_or_results(self):
        cfg = self.single()
        report = execute(cfg, ids=['1'], model_factory=SingleModel)
        folder = Path(cfg['paths']['outputs']) / report['run_id']
        original = (folder / 'submission.xlsx').read_bytes()
        results = (folder / 'results.jsonl').read_bytes()
        exported = export_run(cfg, report['run_id'], answer_profile='numbers')
        self.assertEqual(exported['submission_file'], 'submission-numbers.xlsx')
        self.assertEqual((folder / 'submission.xlsx').read_bytes(), original)
        self.assertEqual((folder / 'results.jsonl').read_bytes(), results)
        self.assertTrue((folder / 'submission-numbers.xlsx').exists())
