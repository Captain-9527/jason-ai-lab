# 索引构建模块：FAISS向量库 + BM25索引
# 每个PDF一对一建立独立的索引文件
import os
import json
import pickle
from typing import List, Union
from pathlib import Path
from tqdm import tqdm
import jieba
import numpy as np

from rank_bm25 import BM25Okapi
import faiss
from tenacity import retry, wait_fixed, stop_after_attempt
import dashscope
from dashscope import TextEmbedding



class BM25Ingestor:
    """BM25索引构建工具，使用jieba中文分词"""

    def __init__(self):
        pass

    def _tokenize(self, text: str) -> List[str]:
        """使用jieba进行中文分词"""
        return list(jieba.cut(text))

    def create_bm25_index(self, chunks: List[str]) -> BM25Okapi:
        """从文本块列表创建BM25索引"""
        tokenized_chunks = [self._tokenize(chunk) for chunk in chunks]
        return BM25Okapi(tokenized_chunks)

    def process_reports(self, all_reports_dir: Path, output_dir: Path):
        """
        批量处理所有报告，生成并保存BM25索引。
        每个PDF对应一个.pkl文件，文件名用sha1命名。
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        all_report_paths = list(Path(all_reports_dir).glob("*.json"))

        for report_path in tqdm(all_report_paths, desc="构建BM25索引"):
            with open(report_path, 'r', encoding='utf-8') as f:
                report_data = json.load(f)

            text_chunks = [chunk['text'] for chunk in report_data['content']['chunks']]
            bm25_index = self.create_bm25_index(text_chunks)

            sha1_name = report_data["metainfo"]["sha1"]
            output_file = output_dir / f"{sha1_name}.pkl"
            with open(output_file, 'wb') as f:
                pickle.dump(bm25_index, f)

        print(f"BM25索引已处理 {len(all_report_paths)} 个报告")


class VectorDBIngestor:
    """FAISS向量库构建工具，使用DashScope Embedding"""

    def __init__(self, embedding_model: str = "text-embedding-v1",
                 max_length: int = 2048):
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.embedding_model = embedding_model
        self.max_length = max_length

    @retry(wait=wait_fixed(20), stop=stop_after_attempt(3))
    def _get_embeddings(self, text_chunks: List[str]) -> List[List[float]]:
        """
        获取文本块的嵌入向量，分批处理。
        DashScope单次最多25条输入。
        """
        if not text_chunks:
            raise ValueError("待嵌入文本列表为空")

        # 过滤空字符串
        text_chunks = [x for x in text_chunks if x.strip()]
        if not text_chunks:
            raise ValueError("所有待嵌入文本均为空字符串")

        embeddings = []
        MAX_BATCH_SIZE = 25

        for i in range(0, len(text_chunks), MAX_BATCH_SIZE):
            batch = text_chunks[i:i + MAX_BATCH_SIZE]
            resp = TextEmbedding.call(
                model=self.embedding_model,
                input=batch
            )
            if 'output' in resp and 'embeddings' in resp['output']:
                for emb in resp['output']['embeddings']:
                    if emb['embedding'] is None or len(emb['embedding']) == 0:
                        raise RuntimeError(
                            f"DashScope返回的embedding为空, text_index={emb.get('text_index')}"
                        )
                    embeddings.append(emb['embedding'])
            elif 'output' in resp and 'embedding' in resp['output']:
                if resp['output']['embedding'] is None or len(resp['output']['embedding']) == 0:
                    raise RuntimeError("DashScope返回的embedding为空")
                embeddings.append(resp['output']['embedding'])
            else:
                raise RuntimeError(f"DashScope embedding API返回格式异常: {resp}")

        return embeddings

    def _create_vector_db(self, embeddings: List[List[float]]):
        """用FAISS构建向量库，采用内积（余弦距离）"""
        embeddings_array = np.array(embeddings, dtype=np.float32)
        dimension = len(embeddings[0])
        index = faiss.IndexFlatIP(dimension)
        index.add(embeddings_array)
        return index

    def _process_report(self, report: dict):
        """针对单份报告，提取文本块并生成向量库"""
        text_chunks = [chunk['text'] for chunk in report['content']['chunks']]
        # 过滤空内容，超长截断
        text_chunks = [t[:self.max_length] for t in text_chunks if len(t) > 0]
        embeddings = self._get_embeddings(text_chunks)
        index = self._create_vector_db(embeddings)
        return index

    def process_reports(self, all_reports_dir: Path, output_dir: Path):
        """
        批量处理所有报告，生成并保存FAISS向量库。
        每个PDF对应一个.faiss文件，文件名用sha1命名。
        """
        all_reports_dir = Path(all_reports_dir)
        output_dir = Path(output_dir)
        all_report_paths = list(all_reports_dir.glob("*.json"))
        output_dir.mkdir(parents=True, exist_ok=True)

        for report_path in tqdm(all_report_paths, desc="构建FAISS向量库"):
            with open(report_path, 'r', encoding='utf-8') as f:
                report_data = json.load(f)
            index = self._process_report(report_data)

            sha1 = report_data["metainfo"].get("sha1", "")
            if not sha1:
                raise ValueError(f"分块报告 {report_path} 缺少sha1字段")
            faiss_file_path = output_dir / f"{sha1}.faiss"
            faiss.write_index(index, str(faiss_file_path))

        print(f"FAISS向量库已处理 {len(all_report_paths)} 个报告")
