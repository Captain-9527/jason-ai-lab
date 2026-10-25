"""从完整表头事实网格确定位置答案，模型不再重复选择答案或扫描行。"""
from copy import deepcopy
from pathlib import Path

from ..schemas import PipelineError
from ..validation.table import bbox
from ..validation.task_scope import header_side


def header_detail_views(pages, model, cfg, image_offset=0):
    """追加顶部放大辅助视图，不替换整页、不假定目标表必在顶部。"""
    if not getattr(model,'artifacts',None):
        return [], []
    from PIL import Image
    from ..infrastructure.storage import digest, file_hash
    folder=Path(model.artifacts).parent/'header_details'
    folder.mkdir(parents=True,exist_ok=True)
    paths, descriptions=[],[]
    for page in pages:
        width,height=page['width'],page['height']
        bottom=min(height,max(200,round(height*.22)))
        scale=min(2,(cfg['processing']['max_pixels']/max(width*bottom,1))**.5)
        if scale<1:
            continue
        target=folder/(digest({'source':file_hash(page['image']),'bottom':bottom,'scale':scale})+'.png')
        with Image.open(page['image']) as original:
            original.crop((0,0,width,bottom)).resize((round(width*scale),round(bottom*scale)),Image.Resampling.LANCZOS).save(target)
        paths.append(str(target))
        descriptions.append({'image_index':len(pages)+image_offset+len(paths),'source_page':page['page'],
                             'source_bbox':[0,0,width,bottom],'scale':scale,
                             'usage':'仅辅助看清顶部层级，目标表仍结合完整页；header_grid.image_size/bbox必须使用pages完整图坐标，不使用本放大图坐标'})
    return paths, descriptions


def header_contract():
    return {'header_grid': {
        'page': '输入页号整数', 'ordinal': '本页目标物理表序号，1起整数',
        'name': '目标表原名或简短位置', 'basis': '目标表位置简短事实',
        'image_size': 'pages中目标完整图的[width,height]整数，不是附加放大图',
        'complete': '布尔值，确认已覆盖全部表头层级而非只列叶子或父层',
        'bands': [{'cells': [{'text': '该行带的原单元格完整文字，按从左到右排列；合并格一次',
                             'role': 'header/blank/title/section_title/branding/data/unit',
                             'bbox': '非空格必填当前完整图pixel [l,t,r,b]；空白格可省略，不造零面积框'}]}],
        'order_rule': '仅说明：bands从第一层到最后一层，坐标顺序由程序核验；不要返回此说明键或重复常量'}}


def assemble_header(q, raw):
    """只依据合法完整事实计算；不修补旧answer，不读取基线或题号。"""
    grid = raw.get('header_grid')
    if not isinstance(grid, dict):
        raise PipelineError('表头事实协议须返回完整header_grid，不能只返回answer或header_scan')
    if (not header_side(q) or grid.get('complete') is not True
            or any(type(grid.get(k)) is not int or grid[k] < 1 for k in ('page','ordinal'))
            or any(not isinstance(grid.get(k),str) or not grid[k].strip() for k in ('name','basis'))
            or not isinstance(grid.get('image_size'),list) or len(grid['image_size']) != 2
            or any(type(n) is not int or n < 1 for n in grid['image_size'])):
        raise PipelineError('表头事实缺少完整性、目标表位置或上传图尺寸声明')
    if any(k in grid for k in ('cells','row_count','col_count')):
        raise PipelineError('表头位置事实使用bands，不重复返回网格尺寸或跨度')
    bands = grid.get('bands')
    if grid.get('bands_order','top_to_bottom') != 'top_to_bottom' or not isinstance(bands,list) or not 1<=len(bands)<=12:
        raise PipelineError('表头行带须按top_to_bottom给出1到12行')
    roles = {'header','blank','title','section_title','branding','data','unit'}
    previous_top = -1
    for band in bands:
        cells = band.get('cells') if isinstance(band,dict) else None
        if not isinstance(cells,list) or not 1<=len(cells)<=80:
            raise PipelineError('每个表头行带须有1到80个单元格')
        if any(not isinstance(c,dict) or not isinstance(c.get('text'),str) or c.get('role') not in roles
               or c['role']=='blank' and c['text'].strip() for c in cells):
            raise PipelineError('表头事实每格须有原文与合法角色，非空内容不能伪装空白')
        nonempty = [c for c in cells if c['text'].strip()]
        boxes = [bbox(c.get('bbox'),*grid['image_size']) for c in nonempty]
        if any(a[0]>=b[0] for a,b in zip(boxes,boxes[1:])):
            raise PipelineError('表头行带单元格必须按原图从左到右排列')
        top = min(box[1] for box in boxes) if boxes else previous_top
        if top < previous_top:
            raise PipelineError('表头行带须按原图从上到下，不按叶子优先重排')
        previous_top = top
        if len(nonempty)>1 and any(c['role'] in {'title','section_title'} for c in nonempty):
            raise PipelineError('同一行带有多个字段/分组，不能当单一整表标题排除部分列父表头')
    first_row = next((r for r,band in enumerate(bands)
                      if any(c['role']=='header' and c['text'].strip() for c in band['cells'])),None)
    if first_row is None:
        raise PipelineError('完整表头事实没有非空字段，不从数据区补造答案')
    row_cells = bands[first_row]['cells']
    eligible = [c for c in row_cells if c['role']=='header' and c['text'].strip()]
    selected = eligible[0] if header_side(q)=='left' else eligible[-1]
    value = deepcopy(raw)
    value['answer'] = selected['text']
    value['evidence'] = [{'page':grid['page'],'bbox_format':'pixel',
                          'image_size':list(grid['image_size']),'bbox':deepcopy(selected.get('bbox'))}]
    preceding = []
    for row in range(first_row):
        covering = bands[row]['cells']
        kinds = {c['role'] for c in covering if c['text'].strip()}
        preceding.append({'row':row,'kind':next(iter(kinds)) if len(kinds)==1 else 'blank' if not kinds else 'data',
                          'text':' '.join(c['text'] for c in covering if c['text'].strip())})
    value['task_checks'] = {
        'table_target':{k:grid[k] for k in ('page','ordinal','name','basis')},
        'header_scan':{'side':header_side(q),'header_row':first_row,'complete':True,
                       'cells':[{'col':index,'text':c['text'],'role':c['role']} for index,c in enumerate(row_cells)],
                       'preceding_rows':preceding,
                       'excluded':[{'text':c['text'],'kind':c['role']} for band in bands for c in band['cells']
                                   if c['role'] not in {'header','blank'} and c['text'].strip()]}}
    value['grounding'] = {'table':grid['name'],'rows':f'header row {first_row}',
                          'columns':selected['text'],'scope':'首个真实表头行，程序按指定方向取非空字段'}
    value['header_grid_check'] = {'source':'header_bands','selected_cell':deepcopy(selected),
                                 'selected_row':first_row,'header_grid':deepcopy(grid)}
    return value
