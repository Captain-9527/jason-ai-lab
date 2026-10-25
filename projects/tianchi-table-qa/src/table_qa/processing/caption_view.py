"""原页竖排题注的受限放大视图；只选择几何，不以PDF文字生成答案。"""
import math
import re
from pathlib import Path

from PIL import Image

from ..infrastructure.storage import digest, file_hash


def vertical_caption_view(q, pages, work, trace, max_pixels=40000000):
    if q.question_type != 'extract' or not re.search(r'竖排.*(?:标题|表名)|(?:标题|表名).*竖排', q.question):
        return None
    numbers = set(re.findall(r'(?:表|Table)\s*(\d+)', q.question, re.I))
    if len(numbers) != 1:
        return None
    pattern = re.compile(r'^表\s*'+re.escape(next(iter(numbers)))+r'(?!\d)')
    choices = []
    grid_count=sum(len(p.get('pdf_vector_grids',[])) for p in pages)
    for p in pages:
        if (p.get('orientation') or {}).get('clockwise_degrees') not in {90,270} or p.get('view_to_page'):
            continue
        for grid in p.get('pdf_vector_grids', []):
            l,t,r,b = grid['bbox']
            words = [w for w in p.get('pdf_words', []) if l <= (w['bbox'][0]+w['bbox'][2])/2 <= r
                     and 0 < t-w['bbox'][3] < p['height']*.12]
            lines = []
            for word in sorted(words,key=lambda w:((w['bbox'][1]+w['bbox'][3])/2,w['bbox'][0])):
                y=(word['bbox'][1]+word['bbox'][3])/2;h=word['bbox'][3]-word['bbox'][1]
                if not lines or abs(y-lines[-1][0]) > max(h,lines[-1][1])*.45:
                    lines.append((y,h,[word]))
                else:lines[-1][2].append(word)
            for _,font,line in lines:
                ordered=sorted(line,key=lambda w:w['bbox'][0])
                text=' '.join(w['text'] for w in ordered)
                # 某些PDF题注字形正常、字符映射却全是标点；仅唯一网格时可依赖外部几何。
                compact=re.sub(r'\s','',text)
                unreliable=(grid_count==1 and len(compact)>=8
                            and not re.search(r'[A-Za-z\u4e00-\u9fff]',compact)
                            and sum(not c.isalnum() for c in compact)>len(compact)*.5)
                if (not pattern.match(text) and not unreliable) or len(ordered)<2:
                    continue
                box=[min(w['bbox'][0] for w in line),min(w['bbox'][1] for w in line),
                     max(w['bbox'][2] for w in line),max(w['bbox'][3] for w in line)]
                # 只接受明确在表外上方的完整单行题注，表内列标题不参与。
                if box[3]>=t or box[2]-box[0] < (r-l)*.2:
                    continue
                area=[max(0,math.floor(box[0]-font)),max(0,math.floor(box[1]-font*.5)),
                      min(p['width'],math.ceil(box[2]+font)),min(p['height'],math.ceil(box[3]+font*.5))]
                choices.append((p,area,unreliable))
    # 重复表号、续表或多页歧义都不猜哪个题注。
    if len(choices)!=1:
        return None
    p,area,unreliable=choices[0];scale=2
    target=Path(work)/'views'/(digest(['vertical_caption',area,file_hash(p['image'])])[:20]+'.png')
    target.parent.mkdir(parents=True,exist_ok=True)
    with Image.open(p['image']) as image:
        cut=image.crop(area)
        size=(cut.width*scale,cut.height*scale)
        if size[0]*size[1] > max_pixels:
            return None
        if not target.exists():cut.resize(size,Image.Resampling.LANCZOS).save(target)
    view={**p,'image':str(target),'width':size[0],'height':size[1],
          'view_to_page':[[1/scale,0,area[0]],[0,1/scale,area[1]],[0,0,1]],
          'view_bbox':area,'caption_focused':True,'selection_basis':'unique_original_vertical_caption'}
    view['caption_text_unreliable']=unreliable
    trace.emit('vertical_caption_view',page=p['page'],bbox=area,scale=scale,width=size[0],height=size[1],
               matching='unique_outer_geometry_unreadable_text' if unreliable else 'caption_number')
    return [view]
