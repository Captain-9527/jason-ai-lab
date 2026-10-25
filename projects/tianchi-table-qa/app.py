"""从源码目录启动，无需先安装本项目为全局包。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from table_qa.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
