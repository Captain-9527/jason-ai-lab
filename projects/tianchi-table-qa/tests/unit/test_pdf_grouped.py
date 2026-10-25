import copy
import unittest
from table_qa.schemas import Question, PipelineError
from table_qa.validation.pdf_grouped import validate_grouped_pdf_array


class PdfGroupedTest(unittest.TestCase):
    def setUp(self):
        words=[]
        def word(text,x,y,width=20):
            words.append({'text':text,'bbox':[x,y,x+width,y+10]})
        word('Alpha Group',250,20,70); word('Beta Group',490,20,80)
        word('Name',10,60,50); word('#',170,60)
        for x,text in zip((220,280,340,460,520,580),('A','B','C','A','B','C')):
            word(text,x,60)
        word('Targetrow',10,120,70); word('3',170,120)
        for x,text in zip((220,280,340,460,520,580),('1','9','0','8','2','0')):
            word(text,x,120)
        self.page={'table_focused':True,'pdf_layout_hint':{'source':'synthetic'},
                   'view_to_page':[[1,0,0],[0,1,0],[0,0,1]],'view_bbox':[0,0,800,300],
                   'pdf_horizontal_rules':[[0,y,800,y] for y in (5,90,295)],'pdf_words':words}
        self.q=Question('s','s.pdf','extract','提取 Targetrow 的 Beta Group A/B/C',answer_format='json_array')
        self.raw={'answer':['8','2','0'],
                  'items':[{'row':'Targetrow','column':'Beta Group '+col,'value':v}
                           for col,v in zip(('A','B','C'),('8','2','0'))]}

    def test_repeated_leaf_columns_are_selected_by_parent(self):
        before=copy.deepcopy(self.raw)
        check=validate_grouped_pdf_array(self.q,self.raw,[self.page])
        self.assertEqual(check['status'],'source_values_supported')
        self.assertEqual(check['parent'],'Beta Group')
        self.assertEqual(self.raw,before)

    def test_values_from_other_group_are_rejected_not_swapped(self):
        self.raw['answer']=['1','9','0']
        with self.assertRaisesRegex(PipelineError,'另一组同名列'):
            validate_grouped_pdf_array(self.q,self.raw,[self.page])
        self.assertEqual(self.raw['answer'],['1','9','0'])

    def test_missing_or_ambiguous_layout_does_not_guess(self):
        self.assertIsNone(validate_grouped_pdf_array(self.q,self.raw,[{**self.page,'table_focused':False}]))
        q=Question('s','s.pdf','extract','提取 Targetrow 的 A/B/C',answer_format='json_array')
        self.assertIsNone(validate_grouped_pdf_array(q,self.raw,[self.page]))
