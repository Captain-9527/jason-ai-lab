"""以合成数据验证单位、精度、前值和保守重跑策略。"""
import copy
import unittest
from dataclasses import replace

from table_qa.schemas import Question, PipelineError
from table_qa.solving.assembly import assemble_response
from table_qa.validation.grounding import validate_prior_array
from table_qa.application.runner import retain_baseline_candidate


class PrecisionAndPriorTest(unittest.TestCase):
    def facts(self, values, **operation):
        return {'records': [{'value': v, 'row': str(i), 'column': '数值', 'level': '数据'}
                            for i, v in enumerate(values)], 'operations': [operation]}

    def test_unrequested_rounding_rejected_but_format_only_kept(self):
        q = Question('s', 's.pdf', 'thinking', '求两个值的平均数', answer_format='number')
        raw = self.facts(['11', '14'], op='mean', digits=0)
        before = copy.deepcopy(raw)
        with self.assertRaisesRegex(PipelineError, '精度损失'):
            assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(raw, before)
        raw['operations'][0]['digits'] = 2
        value, _ = assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(value['answer'], '12.50')
        q = replace(q, question='求平均数，保留0位小数')
        raw['operations'][0]['digits'] = 0
        value, _ = assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(value['answer'], '13')

    def test_percentage_points_are_not_fractional_difference(self):
        q = Question('s', 's.pdf', 'thinking', '费率提高多少百分点', answer_format='number')
        raw = self.facts(['18.6%', '16.3%'], op='difference', digits=1)
        value, _ = assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(value['answer'], '2.3')
        self.assertEqual(value['verifications'][0]['scale'], '100')
        raw['operations'][0]['scale'] = '1'
        with self.assertRaisesRegex(PipelineError, 'scale冲突'):
            assemble_response(q, raw, 'calculation_facts')
        raw = self.facts(['18.6', '16.3'], op='difference')
        value, _ = assemble_response(q, raw, 'calculation_facts')
        self.assertEqual(value['answer'], '2.3')

    def test_prior_values_cannot_be_replaced_with_revised_values(self):
        q = Question('s', 's.pdf', 'extract', '提取：收入前值各年值', answer_format='json_array')
        pages = [{'pdf_words': [{'text': '收入分别为12/15/17亿元（前值为14/16/19亿元）'}]}]
        for answer in [['12', '15', '17'], ['1200', '1500', '1700']]:
            with self.assertRaisesRegex(PipelineError, '以新值替代前值'):
                validate_prior_array(q, {'answer': answer}, pages)
        for answer in [['14', '16', '19'], ['1400', '1600', '1900']]:
            validate_prior_array(q, {'answer': answer}, pages)
        precise = [{'outside_table_context': {'above': '收入分别为12.0/15.0/17.0亿元（前值为14.0/16.0/19.0亿元）'}}]
        with self.assertRaisesRegex(PipelineError, '以新值替代前值'):
            validate_prior_array(q, {'answer': ['1201', '1499', '1702']}, precise)
        close = [{'pdf_words': [{'text': '收入分别为12/15亿元（前值为12.1/15.1亿元）'}]}]
        validate_prior_array(q, {'answer': ['12.1', '15.1']}, close)
        validate_prior_array(q, {'answer': ['12', '15', '17']}, [])
        q = replace(q, question='提取收入调整后值')
        validate_prior_array(q, {'answer': ['12', '15', '17']}, pages)

    def test_unverified_change_fallback_is_explicit_and_switchable(self):
        q = Question('s', 's.pdf', 'extract', '提取数值', answer_format='number')
        old = {'status': 'success', 'answer': '9', 'evidence': [{'page': 1}]}
        current = {'id': 's', 'status': 'uncertain', 'answer': '7',
                   'selected_candidate': {'answer': '7'}, 'warnings': ['定位冲突']}
        before = copy.deepcopy(current)
        self.assertIs(retain_baseline_candidate(q, current, old, 'base', {}), current)
        value = retain_baseline_candidate(q, current, old, 'base', {}, True)
        self.assertEqual(value['answer'], '9')
        self.assertEqual(value['status'], 'uncertain')
        self.assertEqual(value['baseline_fallback_reason'], 'unverified_answer_change')
        self.assertEqual(value['rejected_new_candidate'], {'answer': '7'})
        self.assertEqual(value['evidence'], [])
        self.assertEqual(current, before)
        current['status'] = 'success'
        self.assertIs(retain_baseline_candidate(q, current, old, 'base', {}, True), current)
