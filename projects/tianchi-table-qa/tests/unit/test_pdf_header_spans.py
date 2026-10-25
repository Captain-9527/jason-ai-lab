import copy
import unittest
from table_qa.validation.pdf_header import align_pdf_header_rowspans


class PdfHeaderSpanTest(unittest.TestCase):
    def setUp(self):
        words=[{'text':text,'bbox':[x,y,x+width,y+10]} for text,x,y,width in
               [('Name',20,20,60),('Group',400,20,100),('A',290,60,20),('B',540,60,20)]]
        for y in (100,120,140,160):
            words.extend([{'text':str(y),'bbox':[290,y,310,y+10]},
                          {'text':str(y+1),'bbox':[540,y,560,y+10]}])
        self.page={'table_focused':True,'pdf_layout_hint':{'source':'synthetic'},
                   'view_to_page':[[1,0,0],[0,1,0],[0,0,1]],'view_bbox':[0,-5,700,205],
                   'pdf_horizontal_segments':[[0,y,700,y] for y in (5,80,200)],'pdf_words':words}
        self.raw={'answer':{'row_count':6,'col_count':3,'cells':[
            {'text':'Name','row':0,'col':0,'rowspan':1,'colspan':1},
            {'text':'Group','row':0,'col':1,'rowspan':1,'colspan':2},
            {'text':'A','row':1,'col':1,'rowspan':1,'colspan':1},
            {'text':'B','row':1,'col':2,'rowspan':1,'colspan':1}]},
            'full_table_check':{'row_breakdown':{'header':2,'body':4,'notes':0}}}

    def test_exact_source_and_unbroken_column_support_missing_rowspan(self):
        before=copy.deepcopy(self.raw)
        value,audit=align_pdf_header_rowspans(self.raw,[self.page])
        self.assertEqual(value['answer']['cells'][0]['rowspan'],2)
        self.assertEqual(value['answer']['row_count'],6)
        self.assertEqual(self.raw,before)
        self.assertEqual(audit['original_answer'],before['answer'])
        self.assertIsNone(align_pdf_header_rowspans(value,[self.page])[1])

    def test_rule_or_wrong_text_prevents_extension(self):
        page={**self.page,'pdf_horizontal_segments':self.page['pdf_horizontal_segments']+[[0,60,170,60]]}
        self.assertIsNone(align_pdf_header_rowspans(self.raw,[page])[1])
        wrong=copy.deepcopy(self.raw);wrong['answer']['cells'][0]['text']='Other'
        self.assertIsNone(align_pdf_header_rowspans(wrong,[self.page])[1])
