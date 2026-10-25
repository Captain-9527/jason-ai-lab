"""生成完全虚构的本地输入；不调用模型，不覆盖已有数据。"""
from pathlib import Path

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont


def main():
    root = Path(__file__).resolve().parents[1]
    data = root / 'data'
    # 拒绝覆盖用户已有输入，也不把比赛资料混入演示目录。
    if data.exists():
        raise SystemExit('data/ 已存在，未覆盖；请先备份并移走，或直接配置自己的输入。')
    files = data / 'files'
    files.mkdir(parents=True)
    image = Image.new('RGB', (1000, 600), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    for y in (100, 220, 340, 460):
        draw.line((100, y, 900, y), fill='black', width=3)
    for x in (100, 500, 900):
        draw.line((x, 100, x, 460), fill='black', width=3)
    for row, values in enumerate([('Product', 'Sales'), ('Alpha', '10'), ('Beta', '20')]):
        for col, value in enumerate(values):
            label = Image.new('RGB', (100, 30), 'white')
            ImageDraw.Draw(label).text((0, 5), value, font=font, fill='black')
            image.paste(label.resize((300, 90), Image.Resampling.NEAREST),
                        (130 + col * 400, 125 + row * 120))
    image.save(files / 'sample.png')
    book = Workbook()
    book.active.append(['id', 'file_name', 'question_type', 'question', 'answer_format'])
    for row in [
        ['demo-1', 'sample.png', 'extract', 'Alpha 的 Sales 是多少？', 'number'],
        ['demo-2', 'sample.png', 'thinking', 'Alpha 和 Beta 的 Sales 合计是多少？', 'number'],
        ['demo-3', 'sample.png', 'structure', '恢复完整表格结构，包含表头及数据行。', 'json'],
    ]:
        book.active.append(row)
    book.save(data / 'questions.xlsx')
    book.close()
    print('已生成虚构表格及 3 道题；可执行 python app.py check，无需模型密钥。')


if __name__ == '__main__':
    main()
