# 主流程编排模块，包含路径配置、运行参数配置和Pipeline主类
from dataclasses import dataclass
from pathlib import Path
from src.runtime import DATA_ROOT
import logging
import os
import json
import pandas as pd
import shutil
import time

from src.pdf_mineru import MinerUParser
from src.text_splitter import TextSplitter
from src.ingestion import VectorDBIngestor, BM25Ingestor
from src.questions_processing import QuestionsProcessor


@dataclass
class PipelineConfig:
    """路径配置类，管理所有输入输出目录"""
    def __init__(self, root_path: Path, subset_name: str = "subset.csv",
                 questions_file_name: str = "questions.json",
                 pdf_reports_dir_name: str = "pdf_reports",
                 config_suffix: str = ""):
        self.root_path = root_path
        self.subset_path = root_path / subset_name
        self.questions_file_path = root_path / questions_file_name
        self.pdf_reports_dir = root_path / pdf_reports_dir_name

        self.answers_file_path = root_path / f"answers{config_suffix}.json"
        self.debug_data_path = root_path / "debug_data"
        self.databases_path = root_path / "databases"

        self.vector_db_dir = self.databases_path / "vector_dbs"
        self.documents_dir = self.databases_path / "chunked_reports"
        self.bm25_db_path = self.databases_path / "bm25_dbs"

        self.reports_markdown_dirname = "03_reports_markdown"
        self.reports_markdown_path = self.debug_data_path / self.reports_markdown_dirname


@dataclass
class RunConfig:
    """
    运行参数配置类，集中管理所有可调参数。
    各模块统一从此处读取参数，不再硬编码。
    """
    # === 模型选择 ===
    api_provider: str = "dashscope"
    answering_model: str = "qwen-plus"
    reranking_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v1"

    # === 切分策略（按token数） ===
    chunk_size: int = 300
    chunk_overlap: int = 50
    embedding_max_length: int = 2048

    # === 检索参数 ===
    use_vector_dbs: bool = True
    use_bm25_db: bool = True
    top_n_retrieval: int = 10

    # === 混合检索加权 ===
    llm_reranking: bool = True
    llm_reranking_sample_size: int = 30
    hybrid_llm_weight: float = 0.7
    hybrid_batch_size: int = 10

    # === 其他 ===
    parent_document_retrieval: bool = True
    parallel_requests: int = 4
    submission_file: bool = True
    full_context: bool = False
    config_suffix: str = ""
    pipeline_details: str = ""


class Pipeline:
    """主流程编排类，串联PDF解析、分块、建库、检索、回答等全流程"""

    def __init__(self, root_path: Path, subset_name: str = "subset.csv",
                 questions_file_name: str = "questions.json",
                 pdf_reports_dir_name: str = "pdf_reports",
                 run_config: RunConfig = RunConfig()):
        self.run_config = run_config
        self.paths = PipelineConfig(
            root_path=root_path,
            subset_name=subset_name,
            questions_file_name=questions_file_name,
            pdf_reports_dir_name=pdf_reports_dir_name,
            config_suffix=run_config.config_suffix
        )
        self._convert_json_to_csv_if_needed()

    def _convert_json_to_csv_if_needed(self):
        """检查是否存在subset.json且无subset.csv，若是则自动转换"""
        json_path = self.paths.root_path / "subset.json"
        csv_path = self.paths.root_path / "subset.csv"
        if json_path.exists() and not csv_path.exists():
            try:
                with open(json_path, 'r') as f:
                    data = json.load(f)
                df = pd.DataFrame(data)
                df.to_csv(csv_path, index=False)
            except Exception as e:
                print(f"JSON转CSV出错: {str(e)}")

    # ========== 阶段1：MinerU解析PDF为Markdown ==========

    def export_reports_to_markdown(self):
        """
        批量将pdf_reports目录下所有PDF通过MinerU API解析为Markdown。
        超过200页的PDF会自动拆分后上传。
        """
        pdf_dir = self.paths.pdf_reports_dir
        output_dir = self.paths.reports_markdown_path
        os.makedirs(output_dir, exist_ok=True)

        pdf_files = list(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            print(f"未在 {pdf_dir} 下找到PDF文件")
            return

        parser = MinerUParser()
        for pdf_path in pdf_files:
            base_name = pdf_path.stem
            target_md = output_dir / f"{base_name}.md"
            if target_md.exists():
                print(f"跳过已解析: {base_name}")
                continue
            print(f"开始解析: {pdf_path.name}")
            parser.parse_local_pdf(str(pdf_path), str(output_dir))
            print(f"解析完成: {pdf_path.name}")

    # ========== 阶段2：文本分块 ==========

    def chunk_reports(self):
        """将Markdown报告按token数分块，保留元数据（行号、页码、公司名等）"""
        text_splitter = TextSplitter(
            chunk_size=self.run_config.chunk_size,
            chunk_overlap=self.run_config.chunk_overlap
        )
        print(f"开始分割 {self.paths.reports_markdown_path} 目录下的Markdown文件...")
        print(f"  切分参数: {self.run_config.chunk_size} tokens/chunk, {self.run_config.chunk_overlap} overlap")
        text_splitter.split_markdown_reports(
            all_md_dir=self.paths.reports_markdown_path,
            output_dir=self.paths.documents_dir,
            subset_csv=self.paths.subset_path
        )
        print(f"分割完成，结果已保存到 {self.paths.documents_dir}")

    # ========== 阶段3：创建向量数据库（每PDF一个） ==========

    def create_vector_dbs(self):
        """从分块报告创建FAISS向量数据库"""
        input_dir = self.paths.documents_dir
        output_dir = self.paths.vector_db_dir
        vdb_ingestor = VectorDBIngestor(
            embedding_model=self.run_config.embedding_model,
            max_length=self.run_config.embedding_max_length
        )
        vdb_ingestor.process_reports(input_dir, output_dir)
        print(f"向量数据库已创建: {output_dir}")

    # ========== 阶段4：创建BM25数据库（每PDF一个） ==========

    def create_bm25_dbs(self):
        """从分块报告创建BM25索引"""
        input_dir = self.paths.documents_dir
        output_dir = self.paths.bm25_db_path
        bm25_ingestor = BM25Ingestor()
        bm25_ingestor.process_reports(input_dir, output_dir)
        print(f"BM25数据库已创建: {output_dir}")

    # ========== 完整预处理流程 ==========

    def process_parsed_reports(self):
        """
        处理已解析的报告，完整流程：
        1. Markdown分块
        2. 创建FAISS向量数据库
        3. 创建BM25数据库
        """
        print("开始处理报告流程...")

        print("步骤1：报告分块...")
        self.chunk_reports()

        print("步骤2：创建向量数据库...")
        self.create_vector_dbs()

        print("步骤3：创建BM25数据库...")
        self.create_bm25_dbs()

        print("报告处理流程已完成！")

    # ========== 批量问答 ==========

    def _get_next_available_filename(self, base_path: Path) -> Path:
        """获取下一个可用的文件名，避免覆盖"""
        if not base_path.exists():
            return base_path
        stem = base_path.stem
        suffix = base_path.suffix
        parent = base_path.parent
        counter = 1
        while True:
            new_path = parent / f"{stem}_{counter:02d}{suffix}"
            if not new_path.exists():
                return new_path
            counter += 1

    def process_questions(self):
        """处理所有问题，生成答案文件"""
        processor = QuestionsProcessor(
            vector_db_dir=self.paths.vector_db_dir,
            documents_dir=self.paths.documents_dir,
            bm25_db_dir=self.paths.bm25_db_path,
            questions_file_path=self.paths.questions_file_path,
            subset_path=self.paths.subset_path,
            run_config=self.run_config
        )
        output_path = self._get_next_available_filename(self.paths.answers_file_path)
        _ = processor.process_all_questions(
            output_path=output_path,
            submission_file=self.run_config.submission_file,
            pipeline_details=self.run_config.pipeline_details
        )
        print(f"答案已保存到 {output_path}")

    # ========== 单问即时推理 ==========

    def answer_single_question(self, question: str, kind: str = "string"):
        """单条问题即时推理，返回结构化答案"""
        t0 = time.time()
        print(f"\n{'='*60}")
        print(f"[问答] 问题: {question}")
        print(f"[问答] 配置: model={self.run_config.answering_model}, "
              f"top_k={self.run_config.top_n_retrieval}, "
              f"bm25={'开' if self.run_config.use_bm25_db else '关'}, "
              f"reranking={'开' if self.run_config.llm_reranking else '关'}")
        print(f"{'='*60}")

        # 阶段1：初始化处理器（加载索引、分词器等）
        print("[阶段1] 初始化 QuestionsProcessor ...")
        processor = QuestionsProcessor(
            vector_db_dir=self.paths.vector_db_dir,
            documents_dir=self.paths.documents_dir,
            bm25_db_dir=self.paths.bm25_db_path,
            questions_file_path=None,
            subset_path=self.paths.subset_path,
            run_config=self.run_config
        )
        t1 = time.time()
        print(f"[阶段1] 初始化完成 ({t1 - t0:.2f}s)")
        print(f"  → 对应: questions_processing.py::QuestionsProcessor.__init__()")
        print(f"  → 加载subset映射 + 初始化检索器(FAISS/BM25/Hybrid)")

        # 阶段2：路由识别公司
        companies = processor._route_question_to_companies(question)
        t2 = time.time()
        print(f"[阶段2] 路由识别 ({t2 - t1:.2f}s): 匹配到 {companies}")
        print(f"  → 对应: questions_processing.py::_route_question_to_companies()")

        # 阶段3：检索 + LLM回答（核心耗时）
        print(f"[阶段3] 开始检索+推理 ...")
        print(f"  → 对应: questions_processing.py::process_single_question()")
        print(f"    ├─ retrieval.py::HybridRetriever.retrieve() [向量+BM25检索]")
        print(f"    ├─ reranking.py::LLMReranker.rerank() [LLM重排]")
        print(f"    └─ api_requests.py::get_answer_from_rag_context() [LLM生成答案]")

        answer = processor.process_single_question(question, kind=kind)
        t3 = time.time()
        print(f"[阶段3] 检索+推理完成 ({t3 - t2:.2f}s)")

        # 汇总
        print(f"\n[耗时汇总] 初始化={t1-t0:.2f}s | 路由={t2-t1:.2f}s | 检索+推理={t3-t2:.2f}s | 总计={t3-t0:.2f}s")
        print(f"{'='*60}\n")
        return answer


# ========== 预设配置 ==========

default_config = RunConfig(
    llm_reranking=True,
    use_bm25_db=True,
    parent_document_retrieval=True,
    parallel_requests=4,
    submission_file=True,
    pipeline_details="MinerU解析 + FAISS + BM25混合检索 + LLM重排 + qwen-plus",
    answering_model="qwen-plus",
    reranking_model="qwen-plus",
    config_suffix="_default"
)

fast_config = RunConfig(
    llm_reranking=False,
    use_bm25_db=False,
    parent_document_retrieval=False,
    parallel_requests=4,
    submission_file=True,
    pipeline_details="MinerU解析 + FAISS向量检索 + qwen-plus（快速模式）",
    answering_model="qwen-plus",
    config_suffix="_fast"
)

configs = {
    "default": default_config,
    "fast": fast_config,
}


if __name__ == "__main__":
    root_path = DATA_ROOT
    print('root_path:', root_path)

    pipeline = Pipeline(root_path, run_config=default_config)

    # 1. MinerU解析PDF为Markdown
    print('1. MinerU解析PDF为Markdown')
    # pipeline.export_reports_to_markdown()

    # 2. Markdown分块
    print('2. Markdown分块')
    # pipeline.chunk_reports()

    # 3. 创建向量数据库
    print('3. 创建向量数据库')
    # pipeline.create_vector_dbs()

    # 4. 创建BM25数据库
    print('4. 创建BM25数据库')
    # pipeline.create_bm25_dbs()

    # 5. 处理问题并生成答案
    print('5. 处理问题并生成答案')
    # pipeline.process_questions()

    print('完成')
