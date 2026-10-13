# LLM重排模块，基于大模型对检索结果进行相关性评分和排序
import os
import requests
import src.prompts as prompts
from concurrent.futures import ThreadPoolExecutor
import dashscope
import json
from json_repair import repair_json


class LLMReranker:
    """基于大模型的重排器，支持单条和批量重排"""

    def __init__(self, provider: str = "dashscope", model: str = "qwen-plus"):
        self.provider = provider.lower()
        self.model = model
        self.llm = self._setup_llm()
        self.system_prompt_rerank_single_block = prompts.RerankingPrompt.system_prompt_rerank_single_block
        self.system_prompt_rerank_multiple_blocks = prompts.RerankingPrompt.system_prompt_rerank_multiple_blocks

    def _setup_llm(self):
        """根据provider初始化LLM客户端"""
        if self.provider == "dashscope":
            dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
            return dashscope
        else:
            raise ValueError(f"不支持的LLM provider: {self.provider}")

    def get_rank_for_single_block(self, query: str, retrieved_document: str) -> dict:
        """针对单个文本块，调用LLM进行相关性评分"""
        user_prompt = (
            f'\nHere is the query:\n"{query}"\n\n'
            f'Here is the retrieved text block:\n"""\n{retrieved_document}\n"""\n'
        )
        messages = [
            {"role": "system", "content": self.system_prompt_rerank_single_block},
            {"role": "user", "content": user_prompt},
        ]
        rsp = self.llm.Generation.call(
            model=self.model,
            messages=messages,
            temperature=0,
            result_format='message'
        )
        if not rsp or not hasattr(rsp, 'output'):
            raise RuntimeError(f"DashScope返回异常: {rsp}")

        content = rsp.output.choices[0].message.content
        # 尝试解析JSON
        try:
            content_str = content.strip()
            if content_str.startswith('```'):
                first_nl = content_str.find('\n', 3)
                last_bt = content_str.rfind('```')
                if first_nl > 0 and last_bt > first_nl:
                    content_str = content_str[first_nl + 1:last_bt].strip()
            parsed = json.loads(repair_json(content_str))
            score = float(parsed.get("relevance_score", 0.0))
            reasoning = parsed.get("reasoning", content)
            return {"relevance_score": score, "reasoning": reasoning}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {"relevance_score": 0.5, "reasoning": content}

    def get_rank_for_multiple_blocks(self, query: str, retrieved_documents: list) -> dict:
        """针对多个文本块，批量调用LLM进行相关性评分"""
        formatted_blocks = "\n\n---\n\n".join(
            [f'Block {i + 1}:\n\n"""\n{text}\n"""' for i, text in enumerate(retrieved_documents)]
        )
        user_prompt = (
            f'Here is the query: "{query}"\n\n'
            "Here are the retrieved text blocks:\n"
            f"{formatted_blocks}\n\n"
            f"You should provide exactly {len(retrieved_documents)} rankings, in order."
        )
        messages = [
            {"role": "system", "content": self.system_prompt_rerank_multiple_blocks},
            {"role": "user", "content": user_prompt},
        ]
        rsp = self.llm.Generation.call(
            model=self.model,
            messages=messages,
            temperature=0,
            result_format='message'
        )
        if not rsp or not hasattr(rsp, 'output'):
            raise RuntimeError(f"DashScope返回异常: {rsp}")

        content = rsp.output.choices[0].message.content
        # 尝试解析JSON
        try:
            content_str = content.strip()
            if content_str.startswith('```'):
                first_nl = content_str.find('\n', 3)
                last_bt = content_str.rfind('```')
                if first_nl > 0 and last_bt > first_nl:
                    content_str = content_str[first_nl + 1:last_bt].strip()
            parsed = json.loads(repair_json(content_str))
            if "block_rankings" in parsed:
                return parsed
            # 兼容直接返回列表
            if isinstance(parsed, list):
                return {"block_rankings": parsed}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        # 解析失败时返回默认值
        return {
            "block_rankings": [
                {"relevance_score": 0.5, "reasoning": content}
                for _ in retrieved_documents
            ]
        }

    def rerank_documents(self, query: str, documents: list,
                         documents_batch_size: int = 4,
                         llm_weight: float = 0.7) -> list:
        """
        使用多线程并行方式对多个文档进行重排。
        结合向量相似度和LLM相关性分数，采用加权平均融合。
        """
        doc_batches = [
            documents[i:i + documents_batch_size]
            for i in range(0, len(documents), documents_batch_size)
        ]
        vector_weight = 1 - llm_weight

        if documents_batch_size == 1:
            def process_single_doc(doc):
                ranking = self.get_rank_for_single_block(query, doc['text'])
                doc_with_score = doc.copy()
                doc_with_score["relevance_score"] = ranking["relevance_score"]
                doc_with_score["combined_score"] = round(
                    llm_weight * ranking["relevance_score"] +
                    vector_weight * doc['distance'],
                    4
                )
                return doc_with_score

            with ThreadPoolExecutor(max_workers=1) as executor:
                all_results = list(executor.map(process_single_doc, documents))
        else:
            def process_batch(batch):
                texts = [doc['text'] for doc in batch]
                rankings = self.get_rank_for_multiple_blocks(query, texts)
                results = []
                block_rankings = rankings.get('block_rankings', [])

                if len(block_rankings) < len(batch):
                    print(f"\n警告: 期望 {len(batch)} 个评分但只得到 {len(block_rankings)} 个")
                    for _ in range(len(batch) - len(block_rankings)):
                        block_rankings.append({
                            "relevance_score": 0.0,
                            "reasoning": "默认评分：LLM未返回该文档的评分"
                        })

                for doc, rank in zip(batch, block_rankings):
                    doc_with_score = doc.copy()
                    # 兼容rank为float/int（LLM直接返回分数列表）或dict格式
                    if isinstance(rank, (int, float)):
                        score = float(rank)
                    elif isinstance(rank, dict):
                        score = float(rank.get("relevance_score", 0.0))
                    else:
                        score = 0.0
                    doc_with_score["relevance_score"] = score
                    doc_with_score["combined_score"] = round(
                        llm_weight * score +
                        vector_weight * doc['distance'],
                        4
                    )
                    results.append(doc_with_score)
                return results

            with ThreadPoolExecutor(max_workers=1) as executor:
                batch_results = list(executor.map(process_batch, doc_batches))

            all_results = []
            for batch in batch_results:
                all_results.extend(batch)

        # 按融合分数降序排序
        all_results.sort(key=lambda x: x["combined_score"], reverse=True)
        return all_results
