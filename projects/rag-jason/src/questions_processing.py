# 问题处理模块：路由、检索、LLM回答、比较类问题处理
import os
import json
import re
import time
from typing import List, Dict, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from src.api_requests import APIProcessor
from src.retrieval import VectorRetriever, BM25Retriever, HybridRetriever


class QuestionsProcessor:
    """问题处理器，串联路由、检索、LLM回答全流程"""

    def __init__(self, vector_db_dir: Path, documents_dir: Path,
                 bm25_db_dir: Path,
                 questions_file_path: Optional[Path],
                 subset_path: Path,
                 run_config=None):
        self.vector_db_dir = Path(vector_db_dir)
        self.documents_dir = Path(documents_dir)
        self.bm25_db_dir = Path(bm25_db_dir)
        self.questions_file_path = questions_file_path
        self.subset_path = Path(subset_path)
        self.run_config = run_config

        # 加载subset映射表
        self.company_map = self._load_company_map()

        # 初始化API处理器
        self.api_processor = APIProcessor(
            provider=run_config.api_provider if run_config else "dashscope"
        )

        # 初始化检索器
        self._init_retrievers()

    def _load_company_map(self) -> Dict[str, str]:
        """
        加载subset.csv，建立company_name -> sha1的映射。
        同时建立sha1 -> company_name的反向映射。
        """
        company_to_sha1 = {}
        self.sha1_to_company = {}
        if self.subset_path.exists():
            try:
                df = pd.read_csv(self.subset_path, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(self.subset_path, encoding='gbk')
            for _, row in df.iterrows():
                company = str(row.get('company_name', ''))
                sha1 = str(row.get('sha1', ''))
                if company and sha1:
                    company_to_sha1[company] = sha1
                    self.sha1_to_company[sha1] = company
        return company_to_sha1

    def _init_retrievers(self):
        """根据RunConfig初始化检索器"""
        rc = self.run_config
        embedding_model = rc.embedding_model if rc else "text-embedding-v1"
        api_provider = rc.api_provider if rc else "dashscope"
        reranking_model = rc.reranking_model if rc else "qwen-plus"

        use_bm25 = rc.use_bm25_db if rc else False
        use_reranking = rc.llm_reranking if rc else False
        llm_weight = rc.hybrid_llm_weight if rc else 0.7
        batch_size = rc.hybrid_batch_size if rc else 10

        if use_bm25 and use_reranking:
            # 混合检索 + LLM重排
            self.retriever = HybridRetriever(
                vector_db_dir=self.vector_db_dir,
                documents_dir=self.documents_dir,
                bm25_db_dir=self.bm25_db_dir,
                embedding_model=embedding_model,
                reranking_model=reranking_model,
                api_provider=api_provider,
                llm_weight=llm_weight,
                batch_size=batch_size
            )
            self.retriever_type = "hybrid"
        elif use_bm25:
            # 仅BM25（不常用，一般配合向量）
            self.bm25_retriever = BM25Retriever(self.bm25_db_dir, self.documents_dir)
            self.vector_retriever = VectorRetriever(
                self.vector_db_dir, self.documents_dir, embedding_model
            )
            self.retriever_type = "dual"
        else:
            # 仅向量检索
            self.retriever = VectorRetriever(
                self.vector_db_dir, self.documents_dir, embedding_model
            )
            self.retriever_type = "vector"

    def _route_question_to_companies(self, question: str) -> List[str]:
        """
        从问题文本中提取涉及的公司名，用于路由到对应数据库。
        策略：在问题中匹配subset.csv中的所有公司名。
        """
        matched = []
        for company_name in self.company_map.keys():
            if company_name in question:
                matched.append(company_name)

        # 如果没匹配到任何公司，返回所有公司（全局检索）
        if not matched:
            matched = list(self.company_map.keys())
        return matched

    def _format_rag_context(self, results: List[Dict]) -> str:
        """将检索结果格式化为RAG上下文字符串"""
        if not results:
            return "未找到相关内容。"
        context_parts = []
        for i, r in enumerate(results):
            lines_info = ""
            if r.get("lines"):
                lines_info = f" (行 {r['lines'][0]}-{r['lines'][1]})"
            source_info = f"[来源: {r.get('file_name', '未知')}{lines_info}]"
            context_parts.append(f"--- 片段 {i + 1} {source_info} ---\n{r['text']}")
        return "\n\n".join(context_parts)

    def _retrieve_for_company(self, query: str, sha1: str,
                               top_k: int = 10, sample_size: int = 30) -> List[Dict]:
        """针对单个公司（sha1）执行检索"""
        rc = self.run_config
        use_reranking = rc.llm_reranking if rc else False

        if self.retriever_type == "hybrid":
            return self.retriever.retrieve(
                query, sha1, top_k=top_k,
                sample_size=sample_size,
                use_reranking=use_reranking
            )
        elif self.retriever_type == "dual":
            # 双路检索但不走HybridRetriever
            vec_results = self.vector_retriever.retrieve(query, sha1, top_k=sample_size)
            bm25_results = self.bm25_retriever.retrieve(query, sha1, top_k=sample_size)
            # 简单合并去重
            seen = set()
            merged = []
            for r in vec_results + bm25_results:
                key = (r["sha1"], r["chunk_id"])
                if key not in seen:
                    seen.add(key)
                    merged.append(r)
            merged.sort(key=lambda x: x["distance"], reverse=True)
            return merged[:top_k]
        else:
            return self.retriever.retrieve(query, sha1, top_k=top_k)

    def get_answer_for_company(self, question: str, company_name: str,
                                kind: str = "string") -> Dict:
        """
        针对单个公司回答问题：
        1. 路由到对应sha1
        2. 检索相关内容
        3. 调用LLM生成结构化答案
        """
        sha1 = self.company_map.get(company_name, "")
        if not sha1:
            return {
                "step_by_step_analysis": f"未找到公司 '{company_name}' 的数据库",
                "reasoning_summary": "公司不在知识库中",
                "relevant_pages": [],
                "final_answer": "N/A"
            }

        rc = self.run_config
        top_k = rc.top_n_retrieval if rc else 10
        sample_size = rc.llm_reranking_sample_size if rc else 30
        model = rc.answering_model if rc else "qwen-plus"

        # 全文模式
        if rc and rc.full_context:
            if self.retriever_type in ("hybrid", "vector"):
                vec_ret = self.retriever.vector_retriever if self.retriever_type == "hybrid" else self.retriever
                results = vec_ret.retrieve_all(sha1)
            else:
                results = self.vector_retriever.retrieve_all(sha1)
        else:
            results = self._retrieve_for_company(question, sha1, top_k, sample_size)

        rag_context = self._format_rag_context(results)
        answer = self.api_processor.get_answer_from_rag_context(
            question=question,
            rag_context=rag_context,
            schema=kind,
            model=model
        )

        # 附加公司和来源信息
        answer["company_name"] = company_name
        answer["sha1"] = sha1
        return answer

    def process_single_question(self, question: str, kind: str = "string") -> Dict:
        """
        处理单条问题（Streamlit入口）：
        1. 路由识别涉及的公司
        2. 单公司直接回答，多公司走比较流程
        """
        companies = self._route_question_to_companies(question)

        if len(companies) == 0:
            return {
                "step_by_step_analysis": "未找到匹配的公司",
                "reasoning_summary": "问题中未识别到已有公司",
                "relevant_pages": [],
                "final_answer": "N/A"
            }
        elif len(companies) == 1:
            return self.get_answer_for_company(question, companies[0], kind)
        else:
            return self._process_comparative_question(question, companies, kind)

    def _process_comparative_question(self, question: str,
                                       companies: List[str],
                                       kind: str = "string") -> Dict:
        """
        处理比较类问题：
        1. LLM拆分为各公司独立问题
        2. 并行回答各公司问题
        3. 汇总生成比较结论
        """
        rc = self.run_config
        model = rc.answering_model if rc else "qwen-plus"

        # 拆分问题
        rephrased = self.api_processor.get_rephrased_questions(question, companies)

        # 并行回答各公司
        individual_answers = {}
        max_workers = min(rc.parallel_requests if rc else 2, len(companies))

        def answer_for_company(company):
            q = rephrased.get(company, question)
            return company, self.get_answer_for_company(q, company, kind)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(answer_for_company, c) for c in companies]
            for future in futures:
                company, answer = future.result()
                individual_answers[company] = answer

        # 构建比较上下文
        context_parts = []
        for company, ans in individual_answers.items():
            context_parts.append(
                f"公司: {company}\n"
                f"问题: {rephrased.get(company, question)}\n"
                f"答案: {ans.get('final_answer', 'N/A')}\n"
                f"推理: {ans.get('reasoning_summary', '')}"
            )
        comparative_context = "\n\n---\n\n".join(context_parts)

        # 汇总比较
        comparative_answer = self.api_processor.get_answer_from_rag_context(
            question=question,
            rag_context=comparative_context,
            schema="comparative",
            model=model
        )

        comparative_answer["individual_answers"] = individual_answers
        return comparative_answer

    def _load_questions(self) -> List[Dict]:
        """加载问题文件"""
        if not self.questions_file_path or not Path(self.questions_file_path).exists():
            return []
        with open(self.questions_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def process_all_questions(self, output_path: Path = None,
                               submission_file: bool = True,
                               pipeline_details: str = "") -> List[Dict]:
        """
        批量处理所有问题，生成答案文件。
        """
        questions = self._load_questions()
        if not questions:
            print("未找到问题或问题文件为空")
            return []

        all_answers = []
        total = len(questions)

        for idx, q in enumerate(questions):
            question_text = q.get("text", "")
            kind = q.get("kind", "string")
            print(f"\n[{idx + 1}/{total}] 处理问题: {question_text[:60]}...")

            try:
                answer = self.process_single_question(question_text, kind)
                answer["question"] = question_text
                answer["kind"] = kind
                all_answers.append(answer)
            except Exception as e:
                print(f"  错误: {e}")
                all_answers.append({
                    "question": question_text,
                    "kind": kind,
                    "step_by_step_analysis": "",
                    "reasoning_summary": f"处理出错: {str(e)}",
                    "relevant_pages": [],
                    "final_answer": "N/A"
                })

            # 每处理5题保存一次进度
            if output_path and (idx + 1) % 5 == 0:
                self._save_answers(all_answers, output_path, pipeline_details)
                print(f"  进度已保存 ({idx + 1}/{total})")

        # 最终保存
        if output_path:
            self._save_answers(all_answers, output_path, pipeline_details)
            print(f"\n全部答案已保存到 {output_path}")

        return all_answers

    def _save_answers(self, answers: List[Dict], output_path: Path,
                       pipeline_details: str = ""):
        """保存答案到JSON文件"""
        output_data = {
            "pipeline_details": pipeline_details,
            "total_questions": len(answers),
            "answers": answers
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
