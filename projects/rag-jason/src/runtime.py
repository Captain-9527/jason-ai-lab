"""固定子项目路径，并加载本项目的本地环境配置。"""
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data" / "stock_data"

# 已有环境变量优先于 .env；不读取父仓库或其他项目的配置。
load_dotenv(PROJECT_ROOT / ".env", override=False)
