# 检索模块：向量检索、BM25检索、混合检索
# 支持按公司名路由到对应的数据库
import os
import json
import pickle
from typing import List, Dict, Optional
from pathlib import Path

import numpy as np
import faiss
import jieba
from tenacity import retry, wait_fixed, stop_after_attempt
import dashscope
from dashscope import TextEmbedding

from src.reranking import LLMReranker



class VectorRetriever:
    """FAISS向量检索器，支持按公司名路由"""

    def __init__(self, vector_db_dir: Path, documents_dir: Path,
                 embedding_model: str = "text-embedding-v1"):
        self.vector_db_dir = Path(vector_db_dir)
        self.documents_dir = Path(documents_dir)
        self.embedding_model = embedding_model
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        # 缓存已加载的索引和文档
        self._index_cache = {}
        self._doc_cache = {}

    def _load_index(self, sha1: str):
        """加载FAISS索引"""
        if sha1 not in self._index_cache:
            faiss_path = self.vector_db_dir / f"{sha1}.faiss"
            if not faiss_path.exists():
                raise FileNotFoundError(f"未找到向量库: {faiss_path}")
            self._index_cache[sha1] = faiss.read_index(str(faiss_path))
        return self._index_cache[sha1]

    def _load_documents(self, sha1: str) -> dict:
        """加载分块文档数据"""
        if sha1 not in self._doc_cache:
            # 遍历查找sha1匹配的json文件
            for json_file in self.documents_dir.glob("*.json"):
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data["metainfo"]["sha1"] == sha1:
                    self._doc_cache[sha1] = data
                    break
            if sha1 not in self._doc_cache:
                raise FileNotFoundError(f"未找到sha1为{sha1}的分块文档")
        return self._doc_cache[sha1]

    @retry(wait=wait_fixed(10), stop=stop_after_attempt(3))
    def _get_query_embedding(self, query: str) -> List[float]:
        """获取查询文本的嵌入向量"""
        resp = TextEmbedding.call(
            model=self.embedding_model,
            input=[query]
        )
        if 'output' in resp and 'embeddings' in resp['output']:
            return resp['output']['embeddings'][0]['embedding']
        raise RuntimeError(f"获取查询embedding失败: {resp}")

    def retrieve(self, query: str, sha1: str, top_k: int = 10) -> List[Dict]:
        """
        向量检索：在指定sha1对应的FAISS库中搜索。
        返回带有text、distance、lines、company_name等元数据的结果列表。
        """
        index = self._load_index(sha1)
        doc_data = self._load_documents(sha1)
        chunks = doc_data["content"]["chunks"]
        company_name = doc_data["metainfo"]["company_name"]
        file_name = doc_data["metainfo"]["file_name"]

        query_emb = self._get_query_embedding(query)
        query_array = np.array([query_emb], dtype=np.float32)

        actual_top_k = min(top_k, index.ntotal)
        distances, indices = index.search(query_array, actual_top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(chunks):
                continue
            chunk = chunks[idx]
            results.append({
                "text": chunk["text"],
                "distance": float(dist),
                "lines": chunk.get("lines", []),
                "chunk_id": chunk.get("id", idx),
                "company_name": company_name,
                "file_name": file_name,
                "sha1": sha1
            })
        return results

    def retrieve_all(self, sha1: str) -> List[Dict]:
        """获取指定sha1的全部文档内容（用于full_context模式）"""
        doc_data = self._load_documents(sha1)
        chunks = doc_data["content"]["chunks"]
        company_name = doc_data["metainfo"]["company_name"]
        file_name = doc_data["metainfo"]["file_name"]
        results = []
        for idx, chunk in enumerate(chunks):
            results.append({
                "text": chunk["text"],
                "distance": 1.0,
                "lines": chunk.get("lines", []),
                "chunk_id": chunk.get("id", idx),
                "company_name": company_name,
                "file_name": file_name,
                "sha1": sha1
            })
        return results


class BM25Retriever:
    """BM25检索器，使用jieba中文分词，支持按公司名路由"""

    def __init__(self, bm25_db_dir: Path, documents_dir: Path):
        self.bm25_db_dir = Path(bm25_db_dir)
        self.documents_dir = Path(documents_dir)
        self._index_cache = {}
        self._doc_cache = {}

    def _load_index(self, sha1: str):
        """加载BM25索引"""
        if sha1 not in self._index_cache:
            pkl_path = self.bm25_db_dir / f"{sha1}.pkl"
            if not pkl_path.exists():
                raise FileNotFoundError(f"未找到BM25索引: {pkl_path}")
            with open(pkl_path, 'rb') as f:
                self._index_cache[sha1] = pickle.load(f)
        return self._index_cache[sha1]

    def _load_documents(self, sha1: str) -> dict:
        """加载分块文档数据"""
        if sha1 not in self._doc_cache:
            for json_file in self.documents_dir.glob("*.json"):
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data["metainfo"]["sha1"] == sha1:
                    self._doc_cache[sha1] = data
                    break
            if sha1 not in self._doc_cache:
                raise FileNotFoundError(f"未找到sha1为{sha1}的分块文档")
        return self._doc_cache[sha1]

    def retrieve(self, query: str, sha1: str, top_k: int = 10) -> List[Dict]:
        """
        BM25检索：在指定sha1对应的BM25索引中搜索。
        返回带有text、score、lines等元数据的结果列表。
        """
        bm25 = self._load_index(sha1)
        doc_data = self._load_documents(sha1)
        chunks = doc_data["content"]["chunks"]
        company_name = doc_data["metainfo"]["company_name"]
        file_name = doc_data["metainfo"]["file_name"]

        tokenized_query = list(jieba.cut(query))
        scores = bm25.get_scores(tokenized_query)

        # 按得分降序排序
        sorted_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in sorted_indices:
            if idx >= len(chunks):
                continue
            chunk = chunks[idx]
            results.append({
                "text": chunk["text"],
                "distance": float(scores[idx]),  # BM25分数
                "lines": chunk.get("lines", []),
                "chunk_id": chunk.get("id", idx),
                "company_name": company_name,
                "file_name": file_name,
                "sha1": sha1
            })
        return results


class HybridRetriever:
    """
    混合检索器：同时调用向量检索和BM25检索，
    合并去重后送入LLM重排，加权融合得分排序。
    """

    def __init__(self, vector_db_dir: Path, documents_dir: Path,
                 bm25_db_dir: Path,
                 embedding_model: str = "text-embedding-v1",
                 reranking_model: str = "qwen-plus",
                 api_provider: str = "dashscope",
                 llm_weight: float = 0.7,
                 batch_size: int = 10):
        self.vector_retriever = VectorRetriever(
            vector_db_dir, documents_dir, embedding_model
        )
        self.bm25_retriever = BM25Retriever(bm25_db_dir, documents_dir)
        self.reranker = LLMReranker(
            provider=api_provider,
            model=reranking_model
        )
        self.llm_weight = llm_weight
        self.batch_size = batch_size

    def retrieve(self, query: str, sha1: str, top_k: int = 10,
                 sample_size: int = 30, use_reranking: bool = True) -> List[Dict]:
        """
        混合检索流程：
        1. 向量检索 top_k * 2
        2. BM25检索 top_k * 2
        3. 合并去重
        4. LLM重排（可选）
        5. 取top_k返回
        """
        import time

        # 双路召回
        t0 = time.time()
        vector_results = self.vector_retriever.retrieve(
            query, sha1, top_k=sample_size
        )
        t1 = time.time()
        print(f"    [检索] FAISS向量召回: {len(vector_results)} 条 ({t1-t0:.2f}s)")

        bm25_results = self.bm25_retriever.retrieve(
            query, sha1, top_k=sample_size
        )
        t2 = time.time()
        print(f"    [检索] BM25关键词召回: {len(bm25_results)} 条 ({t2-t1:.2f}s)")

        # 合并去重（按chunk_id去重）
        seen = set()
        merged = []
        for r in vector_results:
            key = (r["sha1"], r["chunk_id"])
            if key not in seen:
                seen.add(key)
                merged.append(r)
        for r in bm25_results:
            key = (r["sha1"], r["chunk_id"])
            if key not in seen:
                seen.add(key)
                merged.append(r)

        print(f"    [检索] 合并去重后: {len(merged)} 条候选")

        if not merged:
            return []

        # LLM重排
        if use_reranking and len(merged) > 0:
            t3 = time.time()
            reranked = self.reranker.rerank_documents(
                query=query,
                documents=merged,
                documents_batch_size=self.batch_size,
                llm_weight=self.llm_weight
            )
            t4 = time.time()
            print(f"    [重排] LLM重排完成: {len(merged)}→{top_k} 条 ({t4-t3:.2f}s)")
            return reranked[:top_k]
        else:
            # 不重排时按向量distance排序
            merged.sort(key=lambda x: x["distance"], reverse=True)
            return merged[:top_k]
