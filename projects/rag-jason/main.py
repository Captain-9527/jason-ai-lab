# CLI入口，支持各阶段独立运行
import click
from pathlib import Path
from src.runtime import DATA_ROOT
from src.pipeline import Pipeline, configs, default_config


@click.group()
def cli():
    """RAG-Jason 企业知识库问答系统 CLI"""
    pass


@cli.command()
@click.option("--config", default="default", help="配置名称: default, fast")
def parse_pdfs(config):
    """阶段1: MinerU解析PDF为Markdown"""
    root_path = DATA_ROOT
    run_config = configs.get(config, default_config)
    pipeline = Pipeline(root_path, run_config=run_config)
    pipeline.export_reports_to_markdown()


@cli.command()
@click.option("--config", default="default", help="配置名称: default, fast")
def chunk_reports(config):
    """阶段2: Markdown报告分块"""
    root_path = DATA_ROOT
    run_config = configs.get(config, default_config)
    pipeline = Pipeline(root_path, run_config=run_config)
    pipeline.chunk_reports()


@cli.command()
@click.option("--config", default="default", help="配置名称: default, fast")
def create_vector_dbs(config):
    """阶段3: 创建FAISS向量数据库"""
    root_path = DATA_ROOT
    run_config = configs.get(config, default_config)
    pipeline = Pipeline(root_path, run_config=run_config)
    pipeline.create_vector_dbs()


@cli.command()
@click.option("--config", default="default", help="配置名称: default, fast")
def create_bm25_dbs(config):
    """阶段4: 创建BM25索引"""
    root_path = DATA_ROOT
    run_config = configs.get(config, default_config)
    pipeline = Pipeline(root_path, run_config=run_config)
    pipeline.create_bm25_dbs()


@cli.command()
@click.option("--config", default="default", help="配置名称: default, fast")
def process_reports(config):
    """完整预处理: 分块 + 建向量库 + 建BM25库"""
    root_path = DATA_ROOT
    run_config = configs.get(config, default_config)
    pipeline = Pipeline(root_path, run_config=run_config)
    pipeline.process_parsed_reports()


@cli.command()
@click.option("--config", default="default", help="配置名称: default, fast")
def process_questions(config):
    """批量处理问题并生成答案"""
    root_path = DATA_ROOT
    run_config = configs.get(config, default_config)
    pipeline = Pipeline(root_path, run_config=run_config)
    pipeline.process_questions()


@cli.command()
def run_all():
    """执行完整Pipeline: 解析 -> 分块 -> 建库 -> 问答"""
    root_path = DATA_ROOT
    pipeline = Pipeline(root_path, run_config=default_config)
    print("=" * 60)
    print("RAG-Jason 完整Pipeline开始执行")
    print("=" * 60)
    print("\n[1/5] MinerU解析PDF为Markdown...")
    pipeline.export_reports_to_markdown()
    print("\n[2/5] Markdown报告分块...")
    pipeline.chunk_reports()
    print("\n[3/5] 创建FAISS向量数据库...")
    pipeline.create_vector_dbs()
    print("\n[4/5] 创建BM25索引...")
    pipeline.create_bm25_dbs()
    print("\n[5/5] 处理问题并生成答案...")
    pipeline.process_questions()
    print("\n" + "=" * 60)
    print("RAG-Jason Pipeline执行完成！")
    print("=" * 60)


if __name__ == "__main__":
    cli()
