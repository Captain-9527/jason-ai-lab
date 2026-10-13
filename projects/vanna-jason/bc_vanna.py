"""
BC白酒营销助手 — Vanna 核心配置模块
======================================
组合：QianWenAI_Chat（通义千问 LLM）+ ChromaDB_VectorStore（本地向量库）
数据库：MySQL 8.0，lj_consumer_scan 消费者开瓶扫码分区表

Vanna 工作原理（RAG + Text-to-SQL）：
  1. train()     → 将 DDL / 业务文档 / Q-SQL 对写入 ChromaDB 向量库
  2. generate_sql() → 用户问题 → 向量相似检索 → 组装 Prompt → LLM 生成 SQL
  3. run_sql()   → 执行 SQL → 返回 DataFrame
  4. generate_plotly_code() → 自动生成 Plotly 可视化代码

关于表结构的两种获取方式：
  方式 A（手动）：train(ddl=...) 传入已知的建表语句
  方式 B（自动）：connect_to_mysql() 后从 INFORMATION_SCHEMA.COLUMNS 自动拉取
  本模块同时支持两种方式，train_vanna.py 中 --from-db 参数选择方式 B

作者：Jason
"""

import os
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from safety import validate_sql, safe_chart, allow_data
from sqlalchemy.pool import QueuePool

# ── .env 加载 ──
_env_path = Path(__file__).parent / '.env'
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _v = _line.split('=', 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

from vanna.qianwen import QianWenAI_Chat
from vanna.chromadb import ChromaDB_VectorStore


# ════════════════════════════════════════════════
# 核心 Vanna 类：多重继承组合 LLM + VectorStore
# ════════════════════════════════════════════════
class BcVanna(ChromaDB_VectorStore, QianWenAI_Chat):
    """
    白酒营销助手 Vanna 实例。

    MRO：BcVanna → ChromaDB_VectorStore → QianWenAI_Chat → VannaBase

    ChromaDB_VectorStore 负责 RAG：
      - train(ddl/documentation/sql) → 写入三个独立 collection
      - get_related_ddl / get_similar_question_sql → 检索相关知识

    QianWenAI_Chat 负责 LLM：
      - submit_prompt(prompt) → 调用通义千问返回 SQL
      - 底层通过 OpenAI SDK 兼容接口对接 DashScope
    """

    def get_plotly_figure(self, plotly_code=None, df=None, **kwargs):
        # 不执行模型生成的代码，兼容旧版 Flask 图表接口。
        return safe_chart(df)

    def generate_summary(self, question, df, **kwargs):
        if not allow_data():
            return f'查询完成，共 {len(df)} 行；未向模型发送查询结果。'
        return super().generate_summary(question, df, **kwargs)

    def __init__(self, config: Optional[dict] = None):
        chroma_path = os.getenv('CHROMA_PATH', './chroma_db')
        collection_name = os.getenv('CHROMA_COLLECTION', 'bc_vanna')

        chroma_cfg = {
            'path': str((Path(__file__).parent / chroma_path).resolve()),
            'collection_name': collection_name,
        }
        if config:
            chroma_cfg.update(config)

        llm_cfg = {
            'api_key': os.getenv('DASHSCOPE_API_KEY', ''),
            'model': os.getenv('LLM_MODEL', 'qwen-plus'),
            'temperature': 0.1,   # Text-to-SQL 场景低温确保确定性
        }

        merged_cfg = {**llm_cfg, **chroma_cfg}
        ChromaDB_VectorStore.__init__(self, config=merged_cfg)
        QianWenAI_Chat.__init__(self, config=merged_cfg)


# ════════════════════════════════════════════════
# 数据库连接（SQLAlchemy 连接池，自动重连）
# ════════════════════════════════════════════════

# 全局 SQLAlchemy engine 单例
_engine = None


def _get_engine():
    """
    获取 SQLAlchemy engine 单例（QueuePool + pool_pre_ping 自动重连）。
    pool_pre_ping=True：每次取连接前先 SELECT 1 探活，断线则自动替换新连接。
    """
    global _engine
    if _engine is None:
        host     = os.getenv('DB_HOST', '127.0.0.1')
        port     = int(os.getenv('DB_PORT', '3306'))
        user     = os.getenv('DB_USER', '')
        password = os.getenv('DB_PASSWORD', '')
        dbname   = os.getenv('DB_NAME', 'vanna_demo')
        charset  = os.getenv('DB_CHARSET', 'utf8mb4')

        if not user or not password:
            raise ValueError('请在 .env 中设置专用只读数据库账号和密码')
        url = URL.create('mysql+pymysql', username=user, password=password,
                         host=host, port=port, database=dbname,
                         query={'charset': charset, 'connect_timeout': '15'})
        _engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=3,
            max_overflow=5,
            pool_recycle=1800,   # 30分钟强制回收，避免 MySQL wait_timeout
            pool_pre_ping=True,  # 关键：取连接前 ping，自动处理断线重连
            pool_timeout=30,
        )
    return _engine


def connect_db(vn: BcVanna) -> None:
    """
    用 SQLAlchemy 连接池覆写 vn.run_sql，替代原生 pymysql 单连接。
    优势：pool_pre_ping 自动重连，无惧 MySQL wait_timeout 断线。
    """
    engine = _get_engine()

    def run_sql_with_reconnect(sql: str) -> pd.DataFrame:
        """执行 SQL 并返回 DataFrame，连接断线自动重连（由 pool_pre_ping 保障）"""
        validate_sql(sql)
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                conn.exec_driver_sql('START TRANSACTION READ ONLY')
                try:
                    return pd.read_sql(text(sql), conn)
                finally:
                    conn.rollback()
        except Exception:
            raise RuntimeError('数据库查询失败，请检查只读账号权限、连接配置或 SQL') from None

    # 覆写 Vanna 的 run_sql 并标记已设置
    vn.run_sql = run_sql_with_reconnect
    vn.run_sql_is_set = True


# ════════════════════════════════════════════════
# 从数据库自动拉取表结构（无需手工维护 DDL）
# ════════════════════════════════════════════════
def fetch_training_plan_from_db(vn: BcVanna, target_tables: Optional[list] = None):
    """
    通过查询 INFORMATION_SCHEMA.COLUMNS 自动获取表结构，
    生成 Vanna TrainingPlan，无需手动编写 DDL。

    Args:
        vn:            BcVanna 实例（已连接数据库）
        target_tables: 指定只拉取哪些表，None 表示拉取当前数据库所有表

    Returns:
        TrainingPlan 对象，可直接传给 vn.train(plan=...)

    原理：
      SELECT ... FROM INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA = 'vanna_demo'
      → get_training_plan_generic(df) 按表分组生成文档
    """
    db_name = os.getenv('DB_NAME', 'vanna_demo')

    # 查询列信息（含字段注释 COLUMN_COMMENT，MySQL 特有）
    where_clause = f"TABLE_SCHEMA = '{db_name}'"
    if target_tables:
        table_list = ', '.join(f"'{t}'" for t in target_tables)
        where_clause += f" AND TABLE_NAME IN ({table_list})"

    sql = f"""
SELECT
    TABLE_CATALOG,
    TABLE_SCHEMA,
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE,
    CHARACTER_MAXIMUM_LENGTH,
    IS_NULLABLE,
    COLUMN_DEFAULT,
    COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE {where_clause}
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""
    df = vn.run_sql(sql)
    return vn.get_training_plan_generic(df)


# ════════════════════════════════════════════════
# 单例工厂
# ════════════════════════════════════════════════
_instance: Optional[BcVanna] = None


def get_vanna(connect: bool = True) -> BcVanna:
    """
    获取全局唯一的 BcVanna 实例（懒加载）。

    Args:
        connect: True 时自动连接 MySQL（默认）；
                 False 时只初始化 ChromaDB，跳过 MySQL 连接（启动时用）
    """
    global _instance
    if _instance is None:
        _instance = BcVanna()
        if connect:
            connect_db(_instance)
    elif connect and not getattr(_instance, 'run_sql_is_set', False):
        # 已有实例但数据库未连接时，补充连接
        connect_db(_instance)
    return _instance
