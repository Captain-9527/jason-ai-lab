"""合成旋转页题注：不使用比赛题号、文件名或参考答案。"""
import copy
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from table_qa.processing.caption_view import vertical_caption_view
from table_qa.infrastructure.tracing import Trace
from table_qa.schemas import Question


class CaptionViewTest(unittest.TestCase):
    def test_unique_outer_caption_is_scaled_and_mapping_is_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.fixture(folder);before=copy.deepcopy(p)
            q=Question('s','synthetic.pdf','extract','提取表3的原页竖排标题')
            result=vertical_caption_view(q,[p],folder,Trace(Path(folder)/'trace'))[0]
            self.assertTrue(result['caption_focused'])
            self.assertEqual(result['view_to_page'][0][0],.5)
            self.assertEqual(result['width'],2*(result['view_bbox'][2]-result['view_bbox'][0]))
            self.assertEqual(p,before)
            for changed in [{**p,'orientation':{'clockwise_degrees':0}},
                            {**p,'pdf_vector_grids':[]},
                            {**p,'pdf_words':[{'text':'表3','bbox':[20,110,40,120]}]}]:
                self.assertIsNone(vertical_caption_view(q,[changed],folder,Trace(Path(folder)/'trace')))
            self.assertIsNone(vertical_caption_view(q,[p,{**p,'page':2}],folder,Trace(Path(folder)/'trace')))

    def fixture(self,folder):
        image=Path(folder)/'page.png';Image.new('RGB',(400,300),'white').save(image)
        return {'page':1,'image':str(image),'width':400,'height':300,
                'orientation':{'clockwise_degrees':90},'pdf_vector_grids':[{'bbox':[10,100,390,290]}],
                'pdf_words':[{'text':'表','bbox':[60,70,75,80]},
                             {'text':'3','bbox':[80,70,90,80]},
                             {'text':'Synthetic caption','bbox':[100,70,310,80]}]}

    def test_only_explicit_single_vertical_caption_queries_are_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.fixture(folder);trace=Trace(Path(folder)/'trace')
            for text in ['提取表3列名','提取竖排标题','提取表3和表4的竖排标题']:
                self.assertIsNone(vertical_caption_view(Question('s','s.pdf','extract',text),[p],folder,trace))

    def test_unreadable_pdf_text_uses_only_unique_external_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.fixture(folder);trace=Trace(Path(folder)/'trace')
            q=Question('s','s.pdf','extract','提取表3的竖排标题')
            p['pdf_words']=[{'text':'!','bbox':[60,70,75,80]},
                            {'text':'!#%&(!)*+,-./01','bbox':[80,70,310,80]}]
            self.assertTrue(vertical_caption_view(q,[p],folder,trace)[0]['caption_text_unreliable'])
            self.assertIsNone(vertical_caption_view(q,[p,{**p,'page':2}],folder,trace))
            p['pdf_words'][1]['text']='123456789012345'
            self.assertIsNone(vertical_caption_view(q,[p],folder,trace))
