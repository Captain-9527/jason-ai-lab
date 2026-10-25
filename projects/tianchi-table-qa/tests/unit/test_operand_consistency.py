"""逐项原文和操作数冲突，不使用合计的自洽作为取值证明。"""
import copy
import unittest

from table_qa.application.runner import retain_baseline_candidate
from table_qa.application.routing import classify
from table_qa.config import load_config
from table_qa.schemas import Question
from table_qa.validation.consistency import check_document_consistency
from table_qa.validation.grounding import local_checks


class OperandConsistencyTest(unittest.TestCase):
    def test_fragments_in_one_question_are_not_cross_question_conflicts(self):
        q = Question('s', 'synthetic.png', 'thinking', '某时间段有几个任务')
        results = {'s': {'status': 'success', 'answer': '2', 'facts': [
            {'table': '日程', 'row': '下午', 'column': '某天', 'value': '任务A'},
            {'table': '日程', 'row': '下午', 'column': '某天', 'value': '任务B'}]}}
        self.assertEqual(check_document_consistency([q], results), [])
        self.assertEqual(results['s']['status'], 'success')

    def test_most_and_least_are_extremes_not_plain_extraction(self):
        for text in ['从表中提取含量最多的材料','哪些材料杂质最少']:
            q = Question('s','s.pdf','extract',text)
            self.assertEqual(classify(q,load_config())['category'],'filter')

    def test_formula_and_element_count_conflict_is_flagged_not_rewritten(self):
        qs = [Question('a','s.pdf','extract','提取TB9的名义化学成分'),
              Question('b','s.pdf','thinking','计算TB9主要元素种类数（不含 Ti）')]
        results = {'a':{'status':'success','answer':'Ti-2Al-3Mo'},'b':{'status':'success','answer':'1'}}
        conflicts = check_document_consistency(qs,results)
        self.assertEqual(len(conflicts),1)
        self.assertEqual(results['b']['status'],'uncertain')
        self.assertEqual(results['b']['answer'],'1')
        self.assertEqual(conflicts[0]['declared_symbols'],['Al','Mo'])

    def test_parent_group_is_preserved_while_caption_spacing_can_differ(self):
        qs = [Question(str(i), 's.pdf', 'extract', 'q') for i in range(3)]
        def fact(table, column, value):
            return {'table': table, 'row': 'TA9', 'column': column, 'value': value}
        results = {'0': {'status': 'success', 'facts': [fact('表 2 钛合金化学成分', '杂质-O', '.12')]},
                   '1': {'status': 'success', 'facts': [fact('表2 钛及钛合金化学成分', '杂质·不大于 > O', '.10')]},
                   '2': {'status': 'success', 'facts': [fact('表2 钛合金化学成分', '主要成分 > O', '.3')]}}
        conflicts = check_document_consistency(qs, results)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['ids'], ['0', '1'])
        self.assertEqual(results['2']['status'], 'success')

    def test_operand_export_and_rejected_facts_do_not_mutate_source(self):
        q = Question('s', 's.pdf', 'thinking', '求和', route='calculation', answer_format='number')
        candidate = {'answer': '4', 'computation_checked': True, 'evidence': [],
                     'grounding': {'table': 'T', 'rows': 'A,B', 'columns': 'V', 'scope': 'all'},
                     'facts': [{'table': 'T', 'row': 'A', 'column': 'V', 'value': '2'}],
                     'source_records': [{'value': '2', 'row': 'B', 'column': 'V', 'level': '数据'}]}
        before = copy.deepcopy(candidate)
        _, facts = local_checks(q, candidate, [], load_config())
        self.assertEqual(len(facts), 2)
        self.assertEqual(candidate, before)
        record = {'status': 'uncertain', 'answer': '4', 'selected_candidate': candidate, 'facts': facts}
        old = {'status': 'success', 'answer': '5'}
        retained = retain_baseline_candidate(q, record, old, 'base', {}, True)
        self.assertEqual(retained['answer'], '5')
        self.assertEqual(retained['facts'], [])
        self.assertEqual(retained['rejected_new_facts'], facts)
