"""
BC白酒营销助手 — Vanna Flask UI 启动入口
==========================================
使用 VannaFlaskApp 内置 Web UI，开箱即用：
  - Text-to-SQL 对话框
  - 自动生成并执行 SQL
  - 数据表格展示
  - Plotly 图表可视化（自动推断图表类型）
  - 用户纠错反馈（训练数据持续优化）
  - SQL 展示 / 隐藏

启动前请先执行训练脚本：
  python train_vanna.py

启动：
  python app.py
  # 打开浏览器 http://127.0.0.1:8084

作者：Jason
"""

import os
from pathlib import Path

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

from vanna.flask import VannaFlaskApp
from bc_vanna import get_vanna
from safety import allow_data


def main() -> None:
    port  = int(os.getenv('FLASK_PORT', '8084'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    print('=' * 55)
    print('  BC白酒营销助手 — Vanna Text-to-SQL')
    print(f'  http://127.0.0.1:{port}')
    print('=' * 55)
    print('正在初始化 Vanna（加载 ChromaDB 向量库）...')

    vn = get_vanna()

    # 检查训练数据是否已存在
    training_data = vn.get_training_data()
    n = len(training_data) if training_data is not None else 0
    if n == 0:
        print('\n⚠ 向量库为空！请先执行训练脚本：')
        print('    python train_vanna.py')
        print('\n是否现在自动训练？(y/N) ', end='', flush=True)
        try:
            ans = input().strip().lower()
        except EOFError:
            ans = 'n'
        if ans == 'y':
            from train_vanna import train
            train()
        else:
            print('继续启动，但 SQL 生成质量可能较差。')
    else:
        print(f'向量库已就绪，共 {n} 条训练记录 ✓')

    print('\n启动 Flask Web UI...')

    # ── VannaFlaskApp 配置 ──
    app = VannaFlaskApp(
        vn,
        # 界面配置
        title='白酒营销助手',
        subtitle='消费者开瓶扫码数据 · Text-to-SQL · 自动图表',
        logo='https://img.icons8.com/emoji/96/sake-emoji.png',
        # 功能开关
        sql=True,               # 展示生成的 SQL
        table=True,             # 展示数据表格
        chart=True,             # 展示 Plotly 图表
        redraw_chart=True,      # 允许重绘图表
        auto_fix_sql=True,      # SQL 报错时自动修复
        ask_results_correct=True,   # 用户反馈结果是否正确
        followup_questions=False,    # 生成追问建议
        summarization=True,         # 结果文字摘要
        suggested_questions=True,   # 首页建议问题
        show_training_data=True,    # 展示训练数据管理页
        allow_llm_to_see_data=allow_data(), # 允许 LLM 看到查询结果（用于摘要生成）
        csv_download=True,          # 支持下载 CSV
        debug=debug,
    )

    app.run(host='127.0.0.1', port=port, debug=debug)


if __name__ == '__main__':
    main()
