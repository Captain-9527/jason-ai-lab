"""合成左右表头扫描，不包含比赛题号、文件名或提交答案。"""
import copy
import unittest
from dataclasses import replace

from table_qa.config import load_config
from table_qa.schemas import PipelineError, Question
from table_qa.solving.direct import solve_direct
from table_qa.validation.task_scope import audit_task_scope, header_side, task_check_contract, task_requirements


class HeaderScanTest(unittest.TestCase):
    def q(self, side='右'):
        return Question('synthetic','synthetic.png','extract',f'该表最{side}侧的表头或字段名是什么？',answer_format='string',route='metadata')

    def raw(self, side='right'):
        return {'answer':'Metric' if side=='right' else 'Group', 'task_checks': {
            'table_target': {'page':1,'ordinal':1,'name':'top table','basis':'首个匹配表格'},
            'header_scan': {'side':side,'header_row':0,'complete':True,
                'cells':[{'col':0,'text':'','role':'blank'},{'col':1,'text':'Group','role':'header'},
                         {'col':3,'text':'Metric','role':'header'},{'col':4,'text':'','role':'blank'}],
                'preceding_rows':[],
                'excluded':[{'text':'Results panel','kind':'section_title'},{'text':'Publisher','kind':'branding'}]}}}

    def test_both_sides_skip_blank_cells_without_mutating_response(self):
        for side, word in [('left','左'),('right','右')]:
            raw=self.raw(side);before=copy.deepcopy(raw)
            self.assertFalse(audit_task_scope(self.q(word),raw)[0])
            self.assertEqual(raw,before)

    def test_wrong_edge_or_other_level_is_rejected_not_rewritten(self):
        for answer in ['Group','Child metric','Publisher','Results panel']:
            raw=self.raw();raw['answer']=answer
            with self.assertRaisesRegex(PipelineError,'首个非空'):
                audit_task_scope(self.q(),raw)
            self.assertEqual(raw['answer'],answer)

    def test_parent_or_leaf_is_not_globally_preferred(self):
        for row,text in [(0,'Merged group'),(1,'Leaf field')]:
            raw=self.raw();raw['answer']=text
            raw['task_checks']['header_scan'].update(header_row=row,cells=[{'col':0,'text':text,'role':'header'}],
                preceding_rows=[] if row==0 else [{'row':0,'text':'Whole table title','kind':'title'}])
            self.assertFalse(audit_task_scope(self.q(),raw)[0])

    def test_reversed_duplicate_boolean_or_negative_columns_are_rejected(self):
        for columns in [[1,0],[0,0],[True,2],[-1,0]]:
            raw=self.raw()
            raw['task_checks']['header_scan']['cells']=[{'col':c,'text':'Metric','role':'header'} for c in columns]
            with self.assertRaisesRegex(PipelineError,'列号'):
                audit_task_scope(self.q(),raw)

    def test_wrong_direction_row_and_empty_row_are_rejected(self):
        for patch in [{'side':'left'},{'header_row':True},{'header_row':-1},{'cells':[{'col':0,'text':''}]}]:
            raw=self.raw();raw['task_checks']['header_scan'].update(patch)
            with self.assertRaises(PipelineError):
                audit_task_scope(self.q(),raw)

    def test_missing_or_incomplete_declaration_warns(self):
        self.assertTrue(audit_task_scope(self.q(),{'answer':'Metric'})[0])
        raw=self.raw();raw['task_checks']['header_scan']['complete']=False
        self.assertTrue(audit_task_scope(self.q(),raw)[0])

    def test_branding_cannot_be_both_answer_and_exclusion(self):
        raw=self.raw();raw['task_checks']['header_scan']['excluded']=[{'text':'Metric','kind':'branding'}]
        with self.assertRaisesRegex(PipelineError,'排除项'):
            audit_task_scope(self.q(),raw)

    def test_unit_or_data_at_edge_does_not_outrank_a_real_header(self):
        raw=self.raw();raw['answer']='Quantity'
        raw['task_checks']['header_scan']['cells']=[
            {'col':0,'text':'Equipment','role':'title'}, {'col':1,'text':'Quantity','role':'header'},
            {'col':2,'text':'12','role':'data'},{'col':3,'text':'pieces','role':'unit'}]
        self.assertFalse(audit_task_scope(self.q(),raw)[0])
        raw['answer']='pieces'
        with self.assertRaisesRegex(PipelineError,'首个非空'):
            audit_task_scope(self.q(),raw)

    def test_lower_row_cannot_silently_skip_a_group_header(self):
        raw=self.raw();raw['task_checks']['header_scan']['header_row']=1
        for preceding in [[],[{'row':0,'text':'Two parent groups','kind':'header'}]]:
            raw['task_checks']['header_scan']['preceding_rows']=preceding
            with self.assertRaisesRegex(PipelineError,'跳过有效表头'):
                audit_task_scope(self.q(),raw)

    def test_quantity_value_unit_pattern_rejects_unit_mislabeled_as_header(self):
        raw=self.raw();raw['answer']='pieces'
        raw['task_checks']['header_scan']['cells']=[
            {'col':0,'text':'Quantity','role':'header'}, {'col':1,'text':'12','role':'data'},
            {'col':2,'text':'pieces','role':'header'}]
        with self.assertRaisesRegex(PipelineError,'计数单位'):
            audit_task_scope(self.q(),raw)
        raw['task_checks']['header_scan']['cells']=[{'col':0,'text':'pieces','role':'header'}]
        self.assertFalse(audit_task_scope(self.q(),raw)[0])

    def test_structure_and_lists_do_not_get_scalar_header_rules(self):
        for kind,fmt in [('structure','json'),('extract','json_array')]:
            q=replace(self.q(),question_type=kind,answer_format=fmt)
            self.assertIsNone(header_side(q))
            self.assertNotIn('header_scan',task_check_contract(q))

    def test_matching_payload_single_call_and_audit_survives(self):
        q=self.q();raw=self.raw()
        outer=self
        class Model:
            calls=0
            def ask(self,stage,payload,*args,**kwargs):
                self.calls+=1
                outer.assertEqual(payload['response_contract']['task_checks']['header_scan']['side'],'right')
                outer.assertIn('右向左',task_requirements(q,load_config())['header_selection'])
                return raw
        model=Model()
        cfg=load_config();cfg['run']['program_assembly']=False
        result=solve_direct(q,[{'page':1,'width':100,'height':100,'image':'unused'}],model,cfg=cfg)
        self.assertEqual(model.calls,1)
        self.assertEqual(result['answer'],'Metric')
        self.assertTrue(result['task_scope_audit']['task_checks']['header_scan']['complete'])
