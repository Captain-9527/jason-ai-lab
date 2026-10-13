# 文本分块工具类，支持按token分块Markdown文件，保留完整元数据（行号、页码）
# 支持序列化表格独立插入chunk
import json
import re
import tiktoken
from pathlib import Path
from typing import List, Dict, Optional
from langchain_text_splitters import RecursiveCharacterTextSplitter
import pandas as pd
import os


class TextSplitter:
    """
    文本分块工具，支持：
    1. 按token数切分（默认300 token/chunk，50 overlap）
    2. 序列化表格独立插入chunk
    3. 保留行号、页码等定位元数据
    """

    def __init__(self, chunk_size: int = 300, chunk_overlap: int = 50,
                 model_name: str = "gpt-4o", encoding_name: str = "o200k_base"):
        """
        :param chunk_size: 每个分块的最大token数
        :param chunk_overlap: 分块重叠token数
        :param model_name: tiktoken模型名称（用于RecursiveCharacterTextSplitter）
        :param encoding_name: tiktoken编码名称（用于token计数）
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.model_name = model_name
        self.encoding_name = encoding_name
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count_tokens(self, string: str) -> int:
        """统计字符串的token数"""
        return len(self._encoding.encode(string))

    # ========== 页码检测 ==========

    def _detect_pages(self, lines: List[str]) -> List[Dict]:
        """
        检测Markdown中的页码标记，返回每行对应的页码。
        MinerU输出的Markdown通常包含页码标记（如 <!-- Page N --> 或分页符）。
        如果没有页码标记，则根据行数估算页码。
        """
        page_markers = []
        # 匹配常见页码标记模式
        page_pattern = re.compile(r'<!--\s*[Pp]age\s*(\d+)\s*-->')
        current_page = 1

        line_pages = []
        for i, line in enumerate(lines):
            match = page_pattern.search(line)
            if match:
                current_page = int(match.group(1))
            line_pages.append(current_page)

        return line_pages

    # ========== 表格检测 ==========

    def _detect_tables(self, lines: List[str]) -> List[Dict]:
        """
        检测Markdown中的表格区域，支持两种格式：
        1. Markdown表格：连续的 | 开头的行（至少3行）
        2. HTML表格：<table>...</table> 标签块
        返回表格的起止行号（0-indexed）列表。
        """
        tables = []
        i = 0
        total = len(lines)

        while i < total:
            line_stripped = lines[i].strip()

            # 检测HTML表格：包含 <table> 标签的行
            if '<table>' in line_stripped.lower():
                table_start = i
                # 查找 </table> 结束标签（可能在同一行或多行后）
                while i < total:
                    if '</table>' in lines[i].lower():
                        i += 1
                        break
                    i += 1
                table_end = i
                tables.append({'start': table_start, 'end': table_end})

            # 检测Markdown表格：行以 | 开头
            elif line_stripped.startswith('|'):
                table_start = i
                while i < total and lines[i].strip().startswith('|'):
                    i += 1
                table_end = i
                # 至少3行才算表格（表头 + 分隔 + 数据行）
                if table_end - table_start >= 3:
                    tables.append({'start': table_start, 'end': table_end})
            else:
                i += 1

        return tables

    # ========== 按token切分（核心方法，含表格保护） ==========

    def split_markdown_file(self, md_path: Path,
                            serialized_tables: Optional[List[Dict]] = None) -> List[Dict]:
        """
        按token数分割Markdown文件，保留行号和页码元数据。
        表格保护：Markdown表格作为不可分割单元，确保完整性。
        :param md_path: Markdown文件路径
        :param serialized_tables: 序列化表格列表（可选）
        :return: 分块列表
        """
        with open(md_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 检测每行对应的页码
        line_pages = self._detect_pages(lines)
        total_lines = len(lines)

        # 检测表格区域
        tables = self._detect_tables(lines)

        # 将文档分为"文本段"和"表格段"交替的片段
        segments = []  # [(start_line, end_line, is_table), ...]
        prev_end = 0
        for table in tables:
            # 表格前的文本段
            if table['start'] > prev_end:
                segments.append((prev_end, table['start'], False))
            # 表格段
            segments.append((table['start'], table['end'], True))
            prev_end = table['end']
        # 最后的文本段
        if prev_end < total_lines:
            segments.append((prev_end, total_lines, False))

        # 使用LangChain按token切分（仅用于文本段）
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name=self.model_name,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        chunks = []

        for seg_start, seg_end, is_table in segments:
            seg_text = ''.join(lines[seg_start:seg_end])

            if is_table:
                # 表格保护：整个表格作为一个chunk，不切分
                start_page = line_pages[seg_start]
                end_page = line_pages[min(seg_end - 1, total_lines - 1)]
                chunks.append({
                    'text': seg_text,
                    'lines': [seg_start + 1, seg_end],
                    'page_start': start_page,
                    'page_end': end_page,
                    'length_tokens': self.count_tokens(seg_text),
                    'type': 'table'
                })
            else:
                # 普通文本：按token切分
                if not seg_text.strip():
                    continue
                split_texts = text_splitter.split_text(seg_text)

                # 为每个chunk反向定位行号
                seg_full = seg_text
                search_start = 0

                for chunk_text in split_texts:
                    # 查找chunk在段落内的起始位置
                    start_pos = seg_full.find(chunk_text[:100], search_start)
                    if start_pos == -1:
                        start_pos = seg_full.find(chunk_text[:50], max(0, search_start - 500))

                    if start_pos >= 0:
                        # 计算相对于文档的行号
                        lines_before = seg_full[:start_pos].count('\n')
                        start_line = seg_start + lines_before + 1
                        end_line = min(start_line + chunk_text.count('\n'), total_lines)
                        search_start = start_pos + len(chunk_text) // 2
                    else:
                        start_line = seg_start + 1
                        end_line = seg_end

                    start_page = line_pages[min(start_line - 1, total_lines - 1)]
                    end_page = line_pages[min(end_line - 1, total_lines - 1)]

                    chunks.append({
                        'text': chunk_text,
                        'lines': [start_line, end_line],
                        'page_start': start_page,
                        'page_end': end_page,
                        'length_tokens': self.count_tokens(chunk_text),
                        'type': 'content'
                    })

        # 插入序列化表格chunk（如果有外部提供的）
        if serialized_tables:
            table_chunks = self._build_table_chunks(serialized_tables, line_pages, total_lines)
            chunks.extend(table_chunks)

        # 按起始行号排序，确保顺序
        chunks.sort(key=lambda c: c['lines'][0])

        # 分配id
        for idx, chunk in enumerate(chunks):
            chunk['id'] = idx

        return chunks

    # ========== 序列化表格处理 ==========

    def _build_table_chunks(self, serialized_tables: List[Dict],
                            line_pages: List[int],
                            total_lines: int) -> List[Dict]:
        """
        将序列化表格构建为独立chunk。
        :param serialized_tables: 表格数据列表，每项含 page, text 或 information_blocks
        :param line_pages: 每行对应页码
        :param total_lines: 总行数
        :return: 表格chunk列表
        """
        table_chunks = []
        for table in serialized_tables:
            # 提取表格文本
            if 'serialized' in table and 'information_blocks' in table['serialized']:
                table_text = "\n".join(
                    block["information_block"]
                    for block in table["serialized"]["information_blocks"]
                )
            elif 'text' in table:
                table_text = table['text']
            else:
                continue

            page = table.get('page', 1)
            # 估算表格对应的行号范围
            line_start = 1
            for i, lp in enumerate(line_pages):
                if lp >= page:
                    line_start = i + 1
                    break

            table_chunks.append({
                'text': table_text,
                'lines': [line_start, min(line_start + 10, total_lines)],
                'page_start': page,
                'page_end': page,
                'length_tokens': self.count_tokens(table_text),
                'type': 'serialized_table',
                'table_id': table.get('table_id', '')
            })

        return table_chunks

    # ========== 批量处理 ==========

    def split_markdown_reports(self, all_md_dir: Path, output_dir: Path,
                               chunk_size: int = None, chunk_overlap: int = None,
                               subset_csv: Path = None,
                               serialized_tables_dir: Optional[Path] = None):
        """
        批量处理目录下所有Markdown文件，按token分块并输出为JSON文件。
        :param all_md_dir: 存放.md文件的目录
        :param output_dir: 输出.json文件的目录
        :param chunk_size: 每个分块的最大token数（覆盖构造时的默认值）
        :param chunk_overlap: 分块重叠token数（覆盖构造时的默认值）
        :param subset_csv: subset.csv路径
        :param serialized_tables_dir: 序列化表格目录（可选）
        """
        # 允许运行时覆盖参数
        if chunk_size is not None:
            self.chunk_size = chunk_size
        if chunk_overlap is not None:
            self.chunk_overlap = chunk_overlap

        # 建立 file_name（去扩展名）到 company_name/sha1 的映射
        file2company = {}
        file2sha1 = {}
        if subset_csv is not None and os.path.exists(subset_csv):
            try:
                df = pd.read_csv(subset_csv, encoding='utf-8')
            except UnicodeDecodeError:
                print('警告：subset.csv 不是 utf-8 编码，自动尝试 gbk 编码...')
                df = pd.read_csv(subset_csv, encoding='gbk')
            if 'file_name' in df.columns:
                for _, row in df.iterrows():
                    file_no_ext = os.path.splitext(str(row['file_name']))[0]
                    file2company[file_no_ext] = row['company_name']
                    if 'sha1' in row:
                        file2sha1[file_no_ext] = row['sha1']
            elif 'sha1' in df.columns:
                for _, row in df.iterrows():
                    file_no_ext = str(row['sha1'])
                    file2company[file_no_ext] = row['company_name']
                    file2sha1[file_no_ext] = row['sha1']
            else:
                raise ValueError('subset.csv 缺少 file_name 或 sha1 列')

        all_md_dir = Path(all_md_dir)
        output_dir = Path(output_dir)
        all_md_paths = list(all_md_dir.glob("*.md"))
        output_dir.mkdir(parents=True, exist_ok=True)

        for md_path in all_md_paths:
            # 加载序列化表格（如果有）
            serialized_tables = None
            if serialized_tables_dir:
                table_path = serialized_tables_dir / (md_path.stem + ".json")
                if table_path.exists():
                    with open(table_path, 'r', encoding='utf-8') as f:
                        table_data = json.load(f)
                    serialized_tables = table_data.get('tables', [])

            chunks = self.split_markdown_file(md_path, serialized_tables)

            output_json_path = output_dir / (md_path.stem + ".json")
            file_no_ext = md_path.stem
            company_name = file2company.get(file_no_ext, "")
            sha1 = file2sha1.get(file_no_ext, "")

            metainfo = {
                "sha1": sha1,
                "company_name": company_name,
                "file_name": md_path.name
            }

            # 统计信息
            content_chunks = [c for c in chunks if c['type'] == 'content']
            table_chunks = [c for c in chunks if c['type'] == 'table']
            ser_table_chunks = [c for c in chunks if c['type'] == 'serialized_table']

            with open(output_json_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "metainfo": metainfo,
                    "content": {"chunks": chunks}
                }, f, ensure_ascii=False, indent=2)
            print(f"已处理: {md_path.name} -> {output_json_path.name} "
                  f"({len(content_chunks)} 文本 + {len(table_chunks)} 表格 "
                  f"+ {len(ser_table_chunks)} 序列化表格, "
                  f"共{sum(c['length_tokens'] for c in chunks)} tokens)")

        print(f"共分割 {len(all_md_paths)} 个Markdown文件")
