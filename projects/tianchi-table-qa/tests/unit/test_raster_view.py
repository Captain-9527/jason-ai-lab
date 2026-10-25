"""嵌入图像题注和正文分离；合成资料，无比赛题号或模型调用。"""
import io
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
import pymupdf
from PIL import Image, ImageDraw
from table_qa.config import load_config
from table_qa.schemas import Question
from table_qa.infrastructure.tracing import Trace
from table_qa.processing.document import prepare_document
from table_qa.processing.views import enrich_pages,question_views,captioned_raster_view


class RasterViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.cfg=load_config();self.cfg['paths']['cache']=str(self.root/'cache')
        self.cfg['processing'].update(dpi=72,preprocess=False,pdf_auto_orient=False)
        image=Image.new('RGB',(700,300),'white');draw=ImageDraw.Draw(image)
        for y in (0,60,120,180,240,299):draw.line((0,y,699,y),fill='black')
        buffer=io.BytesIO();image.save(buffer,format='PNG')
        source=self.root/'synthetic.pdf'
        with pymupdf.open() as doc:
            page=doc.new_page(width=600,height=500)
            page.insert_text((50,35),'Old forecast: 12.5 units; revised forecast: 10.0 units.')
            page.insert_text((50,80),'Figure 7: Forecast breakdown')
            page.insert_image(pymupdf.Rect(50,100,550,330),stream=buffer.getvalue(),keep_proportion=False)
            page.insert_text((50,400),'Body note outside the chart, not a cell.')
            doc.save(source)
        self.trace=Trace(self.root/'trace.jsonl')
        self.pages=enrich_pages(source,prepare_document(source,self.cfg,self.trace),self.cfg,self.root,self.trace)
        self.q=Question('s','synthetic.pdf','structure','恢复 Figure 7 的表头',route='structure')

    def test_unique_embedded_image_is_cropped_but_body_context_is_retained(self):
        view=question_views(self.q,self.pages,self.cfg,self.root,self.trace)[0]
        self.assertTrue(view['embedded_raster']);self.assertFalse(view['table_focused'])
        self.assertEqual(view['view_bbox'],[50,100,550,330])
        self.assertEqual(view['view_to_page'],[[1,0,50],[0,1,100],[0,0,1]])
        self.assertIn('12.5',view['outside_table_context']['above'])
        self.assertIn('not a cell',view['outside_table_context']['below'])

    def test_wrong_caption_or_ambiguous_images_do_not_crop(self):
        q=replace(self.q,question='恢复 Figure 8 的表头')
        self.assertIsNone(captioned_raster_view(q,self.pages[0],self.root,self.trace))
        p=self.pages[0]
        self.assertIsNone(captioned_raster_view(self.q,{**p,'pdf_rasters':p['pdf_rasters']*2},self.root,self.trace))
        self.cfg['run']['focus_ruled_pdf_tables']=False
        self.assertNotIn('embedded_raster',question_views(self.q,self.pages,self.cfg,self.root,self.trace)[0])

    def test_filled_segmented_rectangle_rules_form_one_table(self):
        source=self.root/'rect-lines.pdf'
        with pymupdf.open() as doc:
            page=doc.new_page(width=600,height=600)
            page.insert_text((50,75),'Table 16')
            for area in [(50,100,550,100.5),(50,140,300,140.5),
                         (300,140,550,140.5),(50,280,550,280.5)]:
                page.draw_rect(pymupdf.Rect(area),color=None,fill=(0,0,0))
            for x,text in [(55,'Name'),(300,'Alpha'),(480,'Beta')]:page.insert_text((x,125),text)
            for i,y in enumerate((170,210,250)):
                page.insert_text((55,y),'Row '+str(i))
                page.insert_text((310,y),str(i+2));page.insert_text((500,y),str(i+4))
            doc.save(source)
        pages=enrich_pages(source,prepare_document(source,self.cfg,self.trace),self.cfg,self.root,self.trace)
        self.assertEqual(len(pages[0]['pdf_horizontal_rules']),3)
        q=replace(self.q,question='恢复 Table 16 的表头')
        view=question_views(q,pages,self.cfg,self.root,self.trace)[0]
        self.assertTrue(view['table_focused'])
        self.assertEqual(view['pdf_layout_hint']['body_text_row_count'],3)
