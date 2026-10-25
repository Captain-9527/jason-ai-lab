"""方向、尺寸契约及旧候选回退的确定性检查。"""
import copy
import unittest
from unittest.mock import Mock

import numpy as np
import pymupdf
from PIL import Image

from table_qa.application.pipeline import review
from table_qa.application.runner import retain_baseline_candidate
from table_qa.config import load_config, validate_config
from table_qa.processing.document import pdf_orientation, rotate_quadrant
from table_qa.schemas import Question, PipelineError
from table_qa.solving.direct import solve_direct
from table_qa.infrastructure.metrics import efficiency_stats


class OrientationTest(unittest.TestCase):
    def test_pdf_rotation_counted_once_and_four_directions(self):
        opts = {"pdf_auto_orient": True, "orientation_min_chars": 10, "orientation_min_ratio": .9}
        for inserted, pdf_rot, expected in [(0,0,0), (90,0,90), (180,0,180), (270,0,270), (90,90,0)]:
            with pymupdf.open() as doc:
                page=doc.new_page(width=500,height=500)
                page.insert_text((250,250), "Synthetic orientation", rotate=inserted)
                page.set_rotation(pdf_rot)
                self.assertEqual(pdf_orientation(page,opts)["clockwise_degrees"],expected)
                self.assertEqual(pdf_orientation(page,{"pdf_auto_orient":False})["clockwise_degrees"],0)

    def test_scan_sparse_and_mixed_text_do_not_rotate(self):
        opts={"pdf_auto_orient":True}
        with pymupdf.open() as doc:
            p=doc.new_page()
            self.assertEqual(pdf_orientation(p,opts)["reason"],"insufficient_or_mixed_text")
            p.insert_text((100,200),"Tiny",rotate=90)
            self.assertEqual(pdf_orientation(p,opts)["clockwise_degrees"],0)
            p.insert_text((200,300),"A"*55,rotate=90)
            p.insert_text((10,400),"B"*55)
            self.assertEqual(pdf_orientation(p,opts)["clockwise_degrees"],0)

    def test_rotation_matrix_maps_pixel_centers_to_original(self):
        im=Image.fromarray(np.arange(24,dtype=np.uint8).reshape(4,6))
        for degrees in (0,90,180,270):
            rotated,inverse=rotate_quadrant(im,degrees)
            for y in range(rotated.height):
                for x in range(rotated.width):
                    ox,oy,_=inverse @ [x+.5,y+.5,1]
                    self.assertEqual(rotated.getpixel((x,y)),im.getpixel((int(ox),int(oy))))

    def test_invalid_orientation_config_rejected(self):
        for key,value in [("pdf_auto_orient","yes"),("orientation_min_chars",0),
                          ("orientation_min_ratio",float("nan")),("orientation_min_ratio",.5)]:
            cfg=load_config();cfg["processing"][key]=value
            with self.assertRaises(ValueError):validate_config(cfg)


class StructureContractTest(unittest.TestCase):
    def setUp(self):
        self.q=Question("synthetic","s.png","structure","恢复表头",answer_format="json")
        self.cfg=load_config()
        self.answer={"row_count":37,"col_count":1,"cells":[{"text":"H","row":0,"col":0,"rowspan":1,"colspan":1}]}

    def test_dimensions_conflict_cannot_be_equivalent(self):
        model=Mock()
        candidates={"direct":{"answer":{**self.answer,"row_count":1}},"structured":{"answer":self.answer}}
        passed,result=review(self.q,candidates,[{"page":1}],model,self.cfg)
        self.assertFalse(passed)
        self.assertEqual(result["blocking_risks"][0]["code"],"structure_dimensions_conflict")
        model.ask.assert_not_called()
        stats=efficiency_stats([], {"s":{"review":result,"review_history":[result]}})
        self.assertEqual(stats["candidate_quality"]["reviewed_question_count"],0)

    def test_dimension_revision_allows_explicit_recount_not_silent_shrink(self):
        revision = {"scope": "full_table", "previous": [37, 1], "current": [2, 1],
                    "reason": "此前误取其他表尺寸，目标表为一行表头一行正文",
                    "row_breakdown": {"header": 1, "body": 1, "notes": 0}}
        model = Mock()
        raw = {"answer": {**self.answer, "row_count": 2},
               "evidence": [{"page": 1, "bbox": [0, 0, 100, 100]}]}
        pages = [{"page": 1, "width": 100, "height": 100, "image": "unused"}]
        previous = {"candidates": {"direct": {"answer": self.answer}}}
        for bad in (None, {**revision, "current": [3, 1]}, {**revision, "row_breakdown": {"header": 2, "body": 1, "notes": 0}}):
            model.ask.return_value = {**raw, "dimension_revision": bad}
            with self.subTest(bad=bad), self.assertRaises(PipelineError):
                solve_direct(self.q, pages, model, repair=previous)
        model.ask.return_value = {**raw, "dimension_revision": revision}
        result = solve_direct(self.q, pages, model, repair=previous)
        self.assertEqual(result["answer"]["row_count"], 2)
        self.assertEqual(result["dimension_revision"], revision)

    def test_full_dimensions_required_and_matched(self):
        valid={"scope":"full_table","row_count":37,"col_count":1,"page":1,"basis":"整表37行，非表头高度"}
        for check,expected in [(None,False),({},False),({**valid,"row_count":1},False),
                               ({**valid,"page":99},False),({**valid,"scope":"header"},False),(valid,True)]:
            model=Mock();model.ask.return_value={"verdict":"pass","confidence":.99,"risks":[],"full_table_check":check}
            passed,_=review(self.q,{"repaired":{"answer":self.answer}},[{"page":1,"width":100,"height":100,"image":"unused"}],model,self.cfg)
            self.assertEqual(passed,expected)


class BaselineTest(unittest.TestCase):
    def test_only_missing_answer_uses_legal_old_candidate(self):
        q=Question("s","s.png","extract","数值",answer_format="number")
        old={"status":"success","answer":"0","evidence":[{"page":1,"bbox":[1,2,3,4]}]}
        before=copy.deepcopy(old)
        current={"id":"s","status":"failed","answer":None,"warnings":["timeout"]}
        result=retain_baseline_candidate(q,current,old,"old-run",{})
        self.assertEqual(old,before)
        self.assertIsNone(current["answer"])
        self.assertEqual(result["answer"],"0")
        self.assertEqual(result["status"],"uncertain")
        self.assertEqual(result["evidence"],[])
        self.assertEqual(result["baseline_candidate"]["source_run_id"],"old-run")
        self.assertEqual(result["warnings"][0],"timeout")
        valid={**current,"status":"uncertain","answer":"2"}
        self.assertIs(retain_baseline_candidate(q,valid,old,"old-run",{}),valid)
        for invalid in ["",None,"not-a-number"]:
            self.assertIs(retain_baseline_candidate(q,current,{**old,"answer":invalid},"old-run",{}),current)
