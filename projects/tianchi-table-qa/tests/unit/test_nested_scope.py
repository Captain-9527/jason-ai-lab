"""合成的多表/分组列/嵌套表头，不含比赛题号或答案。"""
import copy
import unittest

from table_qa.config import load_config
from table_qa.schemas import PipelineError, Question
from table_qa.solving.assembly import facts_contract, assemble_response
from table_qa.validation.consistency import check_document_consistency
from table_qa.validation.structure_scope import validate_structure_scope
from table_qa.validation.task_scope import task_check_contract, task_requirements, audit_task_scope


class NestedScopeTest(unittest.TestCase):
    def q(self, text, kind='extract'):
        return Question('synthetic', 'synthetic.png', kind, text, route='lookup', answer_format='string')

    def test_main_table_target_is_optional_for_legacy_but_validated_if_present(self):
        q = self.q('主表最左侧表头')
        self.assertIn('main_table', task_requirements(q, load_config()))
        self.assertIn('table_target', task_check_contract(q))
        self.assertTrue(audit_task_scope(q, {'answer': 'A'})[0])
        target = {'page': 1, 'ordinal': 2, 'name': '表甲', 'basis': '下方独立网格'}
        self.assertEqual(audit_task_scope(q, {'answer': 'A', 'task_checks': {'table_target': target}})[0],
                         ['缺少表头方向扫描声明，未验证指定一侧首个非空字段'])
        target['ordinal'] = True
        with self.assertRaisesRegex(PipelineError, '正整数'):
            audit_task_scope(q, {'answer': 'A', 'task_checks': {'table_target': target}})

    def test_group_values_are_not_replaced_by_the_range(self):
        q = self.q('提取“Pressure levels”对应的值')
        group = {'columns': ['Levels/-1', 'Levels/0', 'Levels/1'], 'values': ['10', '20', '30'],
                 'excluded': ['Range概括范围，不是离散水平'], 'scope_complete': True}
        raw = {'answer': '10, 20, 30', 'task_checks': {'column_group': group}}
        before = copy.deepcopy(raw)
        self.assertFalse(audit_task_scope(q, raw)[0])
        self.assertEqual(raw, before)
        with self.assertRaisesRegex(PipelineError, '叶子列值不一致'):
            audit_task_scope(q, {**raw, 'answer': '10–30'})
        self.assertFalse(task_check_contract(self.q('提取Range对应的值')))
        self.assertFalse(task_check_contract(self.q('提取备注')))
        self.assertFalse(task_check_contract(self.q('提取Pressure levels的Range范围')))
        self.assertFalse(task_check_contract(self.q('Pressure levels有多少个', 'thinking')))

    def nested(self):
        q = self.q('恢复表头行结构', 'structure')
        extent = {'page': 1, 'col_count': 3, 'header_rows': 3, 'note_rows': 0,
                  'header_ranges': [[0, 2], [4, 5]],
                  'header_regions': [[0, 1, 0, 3], [1, 2, 1, 3], [4, 5, 1, 3]],
                  'data_row_labels': ['A', 'B', 'C']}
        cells = [{'text': f'H{r}{c}', 'row': r, 'col': c, 'rowspan': 1, 'colspan': 1}
                 for r in [0, 1, 4] for c in range(3) if r == 0 or c > 0]
        return q, {'table_extent': extent, 'cells': cells}

    def test_nested_headers_keep_offsets_and_exclude_side_body_cells(self):
        q, raw = self.nested()
        self.assertIn('header_ranges', facts_contract(q)[1]['table_extent'])
        self.assertIn('header_regions', facts_contract(q)[1]['table_extent'])
        value, _ = assemble_response(q, raw, 'table_facts')
        self.assertEqual(value['answer']['row_count'], 6)
        validate_structure_scope(q, value['answer'], value['full_table_check'])
        for bad in [value['answer']['cells'][:-1], value['answer']['cells'] + [
                {'text': 'Group body', 'row': 1, 'col': 0, 'rowspan': 1, 'colspan': 1}]]:
            with self.assertRaises(PipelineError):
                validate_structure_scope(q, {**value['answer'], 'cells': bad}, value['full_table_check'])

    def test_invalid_header_regions_are_rejected_without_rewriting(self):
        q, raw = self.nested()
        value, _ = assemble_response(q, raw, 'table_facts')
        for regions in [[], [[0, 1, 0, 4]], [[0, 2, 0, 3], [0, 1, 0, 1]], [[0, 1, 0, 3]]]:
            with self.assertRaises(PipelineError):
                validate_structure_scope(q, value['answer'], {**value['full_table_check'], 'header_regions': regions})

    def test_main_table_conflict_marks_risk_not_new_answers(self):
        qs = [Question(str(i), 'synthetic.png', 'extract', '主表最左侧表头', table_hint='Group') for i in [1, 2]]
        results = {q.id: {'status': 'success', 'answer': 'A', 'selected_candidate': {
            'task_scope_audit': {'task_checks': {'table_target': {'page': 1, 'ordinal': i}}}}}
                   for q, i in zip(qs, [1, 2])}
        self.assertEqual(len(check_document_consistency(qs, results)), 1)
        self.assertTrue(all(r['status'] == 'uncertain' and r['answer'] == 'A' for r in results.values()))
        results['2']['selected_candidate']['task_scope_audit']['task_checks']['table_target']['ordinal'] = 1
        self.assertFalse(check_document_consistency(qs, results))
