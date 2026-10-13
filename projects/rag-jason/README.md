# RAG-Jason 企业知识库问答系统

基于多PDF的RAG检索增强生成问答系统，支持FAISS向量检索 + BM25关键词检索混合模式，LLM重排，结构化答案输出。

个人学习演示项目。仓库提供虚构的 Markdown 报告与问题集，不附带真实财报、预生成索引或服务凭据。

## 核心特性

- **MinerU PDF解析**：通过MinerU在线API批量解析本地PDF为Markdown，支持超200页自动拆分
- **多PDF一对一索引**：每个PDF独立建立FAISS向量库和BM25索引，通过公司名路由检索
- **混合检索**：FAISS向量检索 + BM25关键词检索双路召回，LLM重排融合排序
- **中文BM25优化**：使用jieba中文分词替代空格分词
- **结构化问答**：Pydantic Schema约束LLM输出，支持name/number/boolean/names/string/comparative等多种答案类型
- **元数据保留**：chunk保留行号，支持原文定位展示
- **RunConfig统一调参**：模型选择、切分策略、检索加权等参数集中管理
- **Streamlit UI**：可视化问答界面

## 技术栈

| 组件 | 选型 |
|------|------|
| PDF解析 | MinerU在线API (vlm模式) |
| Embedding | DashScope text-embedding-v1 |
| 向量库 | FAISS (IndexFlatIP) |
| 关键词索引 | BM25Okapi + jieba |
| LLM回答 | qwen-plus (DashScope) |
| LLM重排 | qwen-plus (DashScope) |
| 前端 | Streamlit |

## 目录结构

```text
RAG-Jason/
├── data/stock_data/
│   ├── pdf_reports/          # PDF文件
│   ├── subset.csv            # 公司映射表
│   ├── questions.json        # 问题集
│   └── databases/            # 索引库
├── src/
│   ├── pipeline.py           # 主流程 + RunConfig
│   ├── pdf_mineru.py         # MinerU解析
│   ├── text_splitter.py      # 文本分块
│   ├── ingestion.py          # 索引构建
│   ├── retrieval.py          # 检索 + 路由
│   ├── reranking.py          # LLM重排
│   ├── api_requests.py       # LLM调用封装
│   ├── prompts.py            # 提示词工程
│   └── questions_processing.py
├── app_streamlit.py          # Streamlit UI
├── main.py                   # CLI入口
├── examples/                 # 虚构报告、公司映射和问题集
├── .env.example              # 配置模板
├── .env                      # 本地API Keys（不提交）
└── requirements.txt
```

## 快速开始

### 1. 环境准备

使用 Python 3.11，在本项目独立的虚拟环境中安装依赖：

```bash
cd projects/rag-jason
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2. 配置API Key

执行 `cp .env.example .env`，再填入自己的凭据：

```text
DASHSCOPE_API_KEY=<your-dashscope-key>
MINERU_API_TOKEN=<your-mineru-token>
```

### 3. 准备数据

所有入口都使用本项目下的 `data/stock_data/`，不依赖启动命令时的工作目录。程序自动加载本项目的 `.env`，已设置的环境变量优先。

可以先从虚构的 Markdown 样例开始，跳过 PDF 上传解析：

```bash
mkdir -p data/stock_data/debug_data/03_reports_markdown
cp examples/subset.csv examples/questions.json data/stock_data/
cp examples/示例公司2024年度报告.md data/stock_data/debug_data/03_reports_markdown/
python main.py chunk-reports
python main.py create-bm25-dbs
```

分块和 BM25 建库不需要 API Key；首次使用 tiktoken 时可能需要联网下载分词编码。完成后，配置 DashScope 密钥即可运行：

```bash
python main.py create-vector-dbs
python main.py process-questions
```

向量生成、重排和回答会调用 DashScope，可能产生费用。PDF 解析会将文档上传至 MinerU；请只使用有权处理的数据。索引仅加载自己生成的可信文件。

如果要使用自己的 PDF：

将PDF文件放入 `data/stock_data/pdf_reports/`，并编辑 `data/stock_data/subset.csv`：

```csv
sha1,file_name,company_name
stock_10001,公司A年度报告,公司A
stock_10002,公司B年度报告,公司B
```

### 4. 执行Pipeline

```bash
# 完整流程
python main.py run-all

# 或分步执行
python main.py parse-pdfs         # MinerU解析PDF
python main.py chunk-reports      # 文本分块
python main.py create-vector-dbs  # 建FAISS库
python main.py create-bm25-dbs   # 建BM25库
python main.py process-questions  # 批量问答
```

### 5. 启动Streamlit UI

```bash
streamlit run app_streamlit.py
```

## RunConfig 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `api_provider` | dashscope | API提供商 |
| `answering_model` | qwen-plus | LLM回答模型 |
| `reranking_model` | qwen-plus | LLM重排模型 |
| `embedding_model` | text-embedding-v1 | Embedding模型 |
| `chunk_size` | 300 | 分块 token 数 |
| `chunk_overlap` | 50 | 重叠 token 数 |
| `use_bm25_db` | True | 启用BM25混合检索 |
| `llm_reranking` | True | 启用LLM重排 |
| `hybrid_llm_weight` | 0.7 | LLM重排权重 |
| `top_n_retrieval` | 10 | 最终返回数量 |

## 发布与维护

- `data/` 中的输入文档、解析结果、索引和答案全部忽略；可分享的示例单独保存在 `examples/`。
- 修改示例报告后，应重新分块并构建两种索引。
- `default` 模式启用混合检索与重排；`--config fast` 使用向量检索并关闭重排，仍需要 DashScope。
- 依赖采用版本范围，尚未提供锁定文件；请为本项目使用独立环境。
- FAISS 与 NumPy 使用兼容范围（FAISS < 1.9、NumPy < 2），避免旧 FAISS 与 NumPy 2 的二进制接口冲突。

## 本地检查

```bash
python -m unittest discover -s tests -v
```

测试覆盖目录定位、环境变量优先级及示例数据格式，不调用外部 API。
