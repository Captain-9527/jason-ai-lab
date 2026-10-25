"""合成多级表头、整表分区和确定性方向选择，不使用比赛答案。"""
import copy
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from table_qa.config import load_config
from table_qa.schemas import PipelineError, Question
from table_qa.solving.assembly import assemble_response, facts_contract
from table_qa.solving.direct import solve_direct
from table_qa.validation.task_scope import audit_task_scope
from table_qa.solving.header_facts import header_detail_views
from table_qa.infrastructure.model_client import parse_json


class HeaderFactsTest(unittest.TestCase):
    def q(self, side='右'):
        return Question('s','synthetic.png','extract',f'最{side}侧的表头或字段名是什么？',answer_format='string',route='metadata')

    def cell(self,text,row,col,rowspan=1,colspan=1,role='header'):
        return {'text':text,'role':role,
                'bbox':[col*10,row*10,(col+colspan)*10,(row+rowspan)*10]}

    def grouped(self):
        return {'header_grid':{'page':1,'ordinal':1,'name':'Synthetic results','basis':'首个网格',
            'image_size':[100,100],'complete':True,'bands_order':'top_to_bottom',
            'bands':[{'cells':[self.cell('Name',0,0,2),self.cell('Group Alpha',0,1,colspan=2),
                              self.cell('Group Beta',0,3,colspan=2)]},
                     {'cells':[self.cell('Value',1,1),self.cell('Ratio',1,2),
                               self.cell('Value',1,3),self.cell('Ratio',1,4)]}]}}

    def test_parent_group_is_selected_by_program_without_model_answer(self):
        raw=self.grouped();before=copy.deepcopy(raw)
        value,mode=assemble_response(self.q(),raw,'header_facts')
        self.assertEqual(value['answer'],'Group Beta')
        self.assertEqual(value['header_grid_check']['selected_row'],0)
        self.assertEqual(mode,'program_assembled')
        self.assertFalse(audit_task_scope(self.q(),value)[0])
        self.assertEqual(raw,before)
        self.assertEqual(value['evidence'][0]['bbox'],[30,0,50,10])

    def test_left_nonempty_and_blank_corner_use_same_grid(self):
        raw=self.grouped()
        value,_=assemble_response(self.q('左'),raw,'header_facts')
        self.assertEqual(value['answer'],'Name')
        raw['header_grid']['bands'][0]['cells'][0].update(text='',role='blank')
        value,_=assemble_response(self.q('左'),raw,'header_facts')
        self.assertEqual(value['answer'],'Group Alpha')

    def test_whole_width_section_title_is_skipped_but_leaf_field_kept(self):
        raw={'header_grid':{**self.grouped()['header_grid'],
            'bands':[{'cells':[self.cell('Dataset panel',0,0,colspan=3,role='section_title')]},
                     {'cells':[self.cell('Method',1,0),self.cell('Train set',1,1),self.cell('Metric score',1,2)]}]}}
        value,_=assemble_response(self.q(),raw,'header_facts')
        self.assertEqual(value['answer'],'Metric score')
        self.assertEqual(value['header_grid_check']['selected_row'],1)
        self.assertFalse(audit_task_scope(self.q(),value)[0])

    def test_partial_width_group_cannot_be_excluded_as_title(self):
        for role in ['title','section_title']:
            raw=self.grouped();raw['header_grid']['bands'][0]['cells'][2]['role']=role
            with self.assertRaisesRegex(PipelineError,'部分列父表头'):
                assemble_response(self.q(),raw,'header_facts')

    def test_no_duplicate_model_answer_or_scan_source(self):
        for key,value in [('answer','Child'),('task_checks',{}),('interpretation',{})]:
            with self.assertRaises(PipelineError):
                assemble_response(self.q(),{**self.grouped(),key:value},'header_facts')
        with self.assertRaisesRegex(PipelineError,'header_grid'):
            assemble_response(self.q(),{'answer':'Child'},'header_facts')

    def test_incomplete_empty_duplicate_and_illegal_roles_are_rejected(self):
        variants=[]
        raw=self.grouped();raw['header_grid']['complete']=False;variants.append(raw)
        raw=self.grouped();raw['header_grid']['bands'][0]['cells']=[];variants.append(raw)
        raw=self.grouped();raw['header_grid']['bands'][0]['cells'].append(copy.deepcopy(raw['header_grid']['bands'][0]['cells'][1]));variants.append(raw)
        raw=self.grouped();raw['header_grid']['bands'][0]['cells'][1]['role']='blank';variants.append(raw)
        for raw in variants:
            with self.assertRaises(PipelineError):assemble_response(self.q(),raw,'header_facts')

    def test_multiline_parent_text_is_not_truncated(self):
        raw=self.grouped();raw['header_grid']['bands'][0]['cells'][2]['text']='A long\ncomplete label'
        value,_=assemble_response(self.q(),raw,'header_facts')
        self.assertEqual(value['answer'],'A long\ncomplete label')

    def test_unrelated_questions_keep_existing_fact_routes(self):
        q=replace(self.q(),question='读取普通备注')
        self.assertEqual(facts_contract(q),(None,None))
        self.assertEqual(facts_contract(replace(q,question_type='structure'))[0],'table_facts')

    def test_single_request_does_not_ask_model_for_scan_or_answer(self):
        raw=self.grouped();outer=self
        class Model:
            calls=0
            def ask(self,stage,payload,*args,**kwargs):
                self.calls+=1
                outer.assertEqual(payload['response_mode'],'header_facts')
                outer.assertEqual(set(payload['response_contract']),{'header_grid'})
                return raw
        model=Model()
        candidate=solve_direct(self.q(),[{'page':1,'width':100,'height':100,'image':'unused'}],model,cfg=load_config())
        self.assertEqual(model.calls,1)
        self.assertEqual(candidate['answer'],'Group Beta')
        self.assertEqual(candidate['assembly_mode'],'program_assembled')
        self.assertEqual(candidate['header_grid_check']['selected_cell']['bbox'],[30,0,50,10])

    def test_bands_order_and_geometry_are_checked(self):
        for change in ['reverse_bands','reverse_cells','outside','repeated_dimensions']:
            raw=self.grouped();grid=raw['header_grid']
            if change=='reverse_bands':grid['bands'].reverse()
            elif change=='reverse_cells':grid['bands'][0]['cells'].reverse()
            elif change=='outside':grid['bands'][0]['cells'][0]['bbox']=[-5,0,10,10]
            else:grid['row_count']=2
            with self.assertRaises(PipelineError):assemble_response(self.q(),raw,'header_facts')

    def test_detail_keeps_full_page_coordinates_and_existing_image_offset(self):
        model=Mock(artifacts=Path('/unused/artifacts/model'))
        page={'page':1,'width':100,'height':1000,'image':'unused'}
        with patch('pathlib.Path.mkdir'), patch('PIL.Image.open') as opened, \
                patch('table_qa.infrastructure.storage.file_hash',return_value='source-hash'):
            paths, descriptions=header_detail_views([page],model,load_config(),image_offset=2)
        self.assertEqual(len(paths),1)
        self.assertEqual(descriptions[0]['image_index'],4)
        self.assertEqual(descriptions[0]['source_bbox'],[0,0,100,220])
        self.assertEqual(descriptions[0]['scale'],2)
        opened.return_value.__enter__.return_value.crop.assert_called_once_with((0,0,100,220))
        self.assertEqual(page['image'],'unused')

    def test_duplicate_json_fields_never_discard_parent_header_silently(self):
        for raw in ['{"answer":"first","answer":"last"}',
                    '{"header_grid":{"bands":[{"cells":["parent"],"cells":["child"]}]}}']:
            with self.assertRaisesRegex(PipelineError,'重复字段'):
                parse_json(raw)
        self.assertEqual(parse_json('{"a":{"cells":[]},"b":{"cells":[]}}'),
                         {'a':{'cells':[]},'b':{'cells':[]}})

    def test_blank_cell_box_is_not_used_but_answer_box_stays_strict(self):
        raw=self.grouped()
        raw['header_grid']['bands'][0]['cells'][0].update(text='',role='blank',bbox=[0,0,5,0])
        value,_=assemble_response(self.q(),raw,'header_facts')
        self.assertEqual(value['answer'],'Group Beta')
        raw['header_grid']['bands'][0]['cells'][2]['bbox']=[30,0,50,0]
        with self.assertRaisesRegex(PipelineError,'bbox'):
            assemble_response(self.q(),raw,'header_facts')

    def test_direction_is_checked_from_coordinates_without_redundant_constant(self):
        raw=self.grouped();raw['header_grid'].pop('bands_order')
        value,_=assemble_response(self.q(),raw,'header_facts')
        self.assertEqual(value['answer'],'Group Beta')
        raw['header_grid']['bands_order']='bottom_to_top'
        with self.assertRaises(PipelineError):assemble_response(self.q(),raw,'header_facts')
