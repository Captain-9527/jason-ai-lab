"""合成地区格式、选表、目标匹配、运算绑定与嵌套完整性，不含比赛答案。"""
import copy
import unittest

from table_qa.config import load_config
from table_qa.schemas import Question, PipelineError
from table_qa.solving.assembly import assemble_response
from table_qa.solving.calculator import calculate, numeric
from table_qa.solving.direct import solve_direct
from table_qa.validation.consistency import check_document_consistency
from table_qa.validation.numeric_locale import normalize_numeric_text
from table_qa.validation.operation_scope import audit_semantic_checks, validate_operation_binding
from table_qa.validation.structure_scope import validate_structure_scope
from table_qa.validation.task_scope import apply_answer_conventions
from table_qa.validation.grounding import _numeric_key


class SemanticFamiliesTest(unittest.TestCase):
    def q(self, text='计算', route='calculation', kind='thinking', fmt='number'):
        return Question('synthetic', 'sample.png', kind, text, answer_format=fmt, route=route)

    def test_locale_is_explicit_and_strict(self):
        self.assertEqual(normalize_numeric_text('20.400', 'dot_grouping'), '20400')
        self.assertEqual(normalize_numeric_text('20.400,50', 'dot_grouping'), '20400.50')
        self.assertEqual(normalize_numeric_text('12,50', 'decimal_comma'), '12.50')
        self.assertEqual(numeric('2.238'), numeric('2.238', 'standard'))
        for raw, style in [('1.20','dot_grouping'), ('2.238','decimal_comma'), ('2','guess')]:
            with self.assertRaises(PipelineError):
                normalize_numeric_text(raw, style)

    def test_locale_conversion_is_audited_without_mutation(self):
        q = self.q('请提取“仓库容量”对应的值', 'lookup', 'extract')
        raw = {'answer': '20.400', 'numeric_format': 'dot_grouping', 'numeric_format_basis': '西班牙语同列点分千位'}
        before = copy.deepcopy(raw)
        result = apply_answer_conventions(q, raw, {})
        self.assertEqual(result['answer'], '20400')
        self.assertEqual(result['answer_normalization'][0]['before'], '20.400')
        self.assertEqual(raw, before)
        self.assertEqual(apply_answer_conventions(q, raw, {'numeric_locale':'preserve'}), raw)
        with self.assertRaises(PipelineError):
            apply_answer_conventions(q, {'answer':'20.400','numeric_format':'dot_grouping'}, {})

    def test_single_call_solver_normalizes_explicit_numeric_format(self):
        cfg = load_config()
        class Model:
            calls = 0
            def ask(inner, stage, payload, *args, **kwargs):
                inner.calls += 1
                self.assertIn('numeric_format', payload['response_contract'])
                return {'answer':'20.400', 'numeric_format':'dot_grouping',
                        'numeric_format_basis':'语言和同列分隔符',
                        'task_checks':{'lookup_target':{'requested_label':'仓库容量','matched_label':'仓库容量',
                                      'match_kind':'exact','basis':'原图精确标签'}}}
        model = Model()
        value = solve_direct(self.q('请提取“仓库容量”对应的值', 'lookup', 'extract'),
                             [{'page':1,'width':100,'height':100,'image':'unused'}], model, cfg=cfg)
        self.assertEqual(value['answer'], '20400')
        self.assertEqual(model.calls, 1)

    def test_calculation_uses_per_record_locale_and_signed_accounting(self):
        self.assertEqual(calculate({'op':'sum','numeric_formats':['dot_grouping','dot_grouping']},
                                   ['20.400','1.200']), '21600')
        self.assertEqual(calculate({'op':'difference'}, ['700', '(300)']), '1000')
        self.assertEqual(calculate({'op':'absolute_difference'}, ['20','30']), '10')
        self.assertEqual(calculate({'op':'select','numeric_formats':['decimal_comma','decimal_comma'],
            'predicate':{'operator':'gt','value':'1','missing':'exclude'}}, ['2,5','—'], ['A','B']), ['A'])
        with self.assertRaises(PipelineError):
            calculate({'op':'sum','numeric_formats':['decimal_comma']}, ['1','2'])

    def test_record_locale_is_single_source(self):
        raw = {'records':[{'value':'20.400','row':'A','column':'容量','level':'主类',
                          'numeric_format':'dot_grouping','numeric_format_basis':'同列格式'},
                         {'value':'1.200','row':'B','column':'容量','level':'主类',
                          'numeric_format':'dot_grouping','numeric_format_basis':'同列格式'}],
               'operations':[{'op':'sum'}]}
        before = copy.deepcopy(raw)
        value, _ = assemble_response(self.q(), raw, 'calculation_facts')
        self.assertEqual(value['answer'], '21600')
        self.assertEqual(raw, before)
        raw['operations'][0]['numeric_formats'] = ['standard','standard']
        with self.assertRaisesRegex(PipelineError, '不在operations重复'):
            assemble_response(self.q(), raw, 'calculation_facts')

    def test_cross_question_numeric_keys_share_explicit_locale(self):
        self.assertEqual(_numeric_key('20.400', 'dot_grouping'), _numeric_key('20400'))
        self.assertEqual(_numeric_key('2,50', 'decimal_comma'), _numeric_key('2.50'))
        self.assertNotEqual(_numeric_key('20.400'), _numeric_key('20400'))

    def plan(self, reverse=False):
        records = [{'row':'本年','column':'收入','value':'90'}, {'row':'本年','column':'成本','value':'30'}]
        if reverse:
            records.reverse()
        return {'op':'difference','values':[r['value'] for r in records], 'operand_bindings':records}

    def test_explicit_subtraction_rejects_reversed_fields(self):
        q = self.q('请计算本年收入减本年成本。')
        self.assertFalse(validate_operation_binding(q, self.plan()))
        with self.assertRaisesRegex(PipelineError, '反向'):
            validate_operation_binding(q, self.plan(True))
        with self.assertRaisesRegex(PipelineError, '绝对差'):
            validate_operation_binding(q, {**self.plan(),'op':'absolute_difference'})

    def test_ambiguous_difference_and_first_place_are_not_auto_cleared(self):
        q = self.q('请计算收入与成本的差值。')
        self.assertTrue(validate_operation_binding(q, self.plan()))
        self.assertFalse(validate_operation_binding(q, {**self.plan(),'difference_mode':'signed'}))
        with self.assertRaises(PipelineError):
            validate_operation_binding(q, {**self.plan(),'difference_mode':'absolute'})
        q = self.q('第一名学生是谁', 'lookup', 'extract', 'string')
        self.assertTrue(audit_semantic_checks(q, {'answer':'甲'}))
        self.assertFalse(audit_semantic_checks(q, {'answer':'甲','task_checks':{
            'selection':{'mode':'first_row','basis':'首条记录，原表未按成绩排序'}}}))

    def test_subtraction_periods_cannot_be_reversed(self):
        q = self.q('请计算2024 Revenue减2023 Revenue。')
        records = [{'row':'Revenue','column':'2023','value':'30'},
                   {'row':'Revenue','column':'2024','value':'90'}]
        with self.assertRaisesRegex(PipelineError, '反向'):
            validate_operation_binding(q, {'op':'difference','values':['30','90'],'operand_bindings':records})

    def test_target_match_does_not_silently_replace_near_names(self):
        q = self.q('请提取“李甲分数”对应的值', 'lookup', 'extract')
        exact = {'requested_label':'李甲','matched_label':'李甲','match_kind':'exact','basis':'原图标签'}
        self.assertFalse(audit_semantic_checks(q, {'answer':'90','task_checks':{'lookup_target':exact}}))
        for target in [{**exact,'matched_label':'李乙'}, {**exact,'match_kind':'absent'}]:
            with self.assertRaises(PipelineError):
                audit_semantic_checks(q, {'answer':'90','task_checks':{'lookup_target':target}})
        self.assertTrue(audit_semantic_checks(q, {'answer':'90','task_checks':{
            'lookup_target':{**exact,'match_kind':'synonym','matched_label':'李乙'}}}))

    def test_region_count_checks_actual_regions_not_question_premise(self):
        q = self.q('计算包含RowSpan="2"的两个区域覆盖行数')
        region = {'row':0,'col':0,'rowspan':2,'colspan':1}
        with self.assertRaisesRegex(PipelineError, '前提冲突'):
            audit_semantic_checks(q, {'answer':4,'task_checks':{'region_count':[region]}})
        with self.assertRaisesRegex(PipelineError, '重复'):
            audit_semantic_checks(q, {'answer':4,'task_checks':{'region_count':[region,region]}})
        self.assertFalse(audit_semantic_checks(q, {'answer':4,'task_checks':{
            'region_count':[region,{**region,'col':1}]}}))
        self.assertTrue(audit_semantic_checks(self.q('Row 2-4的黄色区域'), {'answer':'x'}))

    def test_main_table_and_header_without_main_keyword_are_jointly_checked(self):
        qs = [Question('a','s.png','extract','主表列数',table_hint='T',answer_format='number'),
              Question('b','s.png','extract','该表最右侧的表头是什么',table_hint='T',answer_format='string')]
        results = {q.id:{'status':'success','answer':'保持不变','facts':[], 'selected_candidate':{
            'task_scope_audit':{'task_checks':{'table_target':{'page':1,'ordinal':i+1}}}}}
            for i,q in enumerate(qs)}
        before = copy.deepcopy(results)
        self.assertEqual(len(check_document_consistency(qs, results)), 1)
        self.assertTrue(all(r['status']=='uncertain' for r in results.values()))
        self.assertEqual([r['answer'] for r in results.values()], [r['answer'] for r in before.values()])

    def test_nested_group_coverage_rejects_omitted_second_group(self):
        q = self.q('恢复完整表头行结构', kind='structure', fmt='json')
        cells = [{'text':'H','row':r,'col':c,'rowspan':1,'colspan':1}
                 for r in [0,1,4] for c in range(3) if r==0 or c>0]
        answer = {'row_count':7,'col_count':3,'cells':cells}
        groups = [{'label':'甲','header_range':[1,2],'body_range':[2,4],'columns':[1,3]},
                  {'label':'乙','header_range':[4,5],'body_range':[5,7],'columns':[1,3]}]
        check = {'row_breakdown':{'header':3,'body':4,'notes':0}, 'header_ranges':[[0,2],[4,5]],
                 'header_regions':[[0,1,0,3],[1,2,1,3],[4,5,1,3]], 'nested_groups':groups}
        validate_structure_scope(q, answer, check)
        with self.assertRaisesRegex(PipelineError, '未完整覆盖'):
            validate_structure_scope(q, answer, {**check,'nested_groups':groups[:1]})
        with self.assertRaises(PipelineError):
            validate_structure_scope(q, {**answer,'cells':cells[:-1]}, check)


if __name__ == '__main__':
    unittest.main()
