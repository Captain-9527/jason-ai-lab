"""
BC白酒营销助手 — Gradio 统一 UI
==================================
整合三大能力，在同一界面展示：
  Tab 1「数据分析」 ── Vanna Text-to-SQL + Plotly 图表
  Tab 2「智能问答」 ── qwen-agent + 高德/Tavily 双 MCP

MCP 状态面板（右侧边栏）：
  实时显示本轮是否触发了高德/Tavily，并列出具体工具调用详情

技术栈选型原因：
  - Gradio 5.x：比 VannaFlaskApp 可扩展，比 qwen-agent WebUI 更灵活
  - Vanna 只用作 SQL 引擎（generate_sql + run_sql + generate_plotly_code）
  - qwen-agent Assistant 只用作 MCP 调用路由
  - 两套引擎共用同一个界面，互不干扰

作者：Jason
"""

import os
import json
import threading
from pathlib import Path

# ── .env 加载（最先执行）──
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

import gradio as gr
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from bc_vanna import get_vanna
from safety import safe_chart, allow_data
from mcp_tools import McpCallTracker, run_mcp_query, MCP_AMAP, MCP_TAVILY

# ── 全局 tracker（每个会话共用，Tab2 使用）──
_tracker = McpCallTracker()

# ══════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════

def _make_status_html(tracker: McpCallTracker, running: bool = False) -> str:
    """
    生成 MCP 状态面板 HTML。
    running=True 时显示旋转动画（工具调用进行中）。
    """
    amap_color  = '#22c55e' if tracker.amap_called   else '#6b7280'
    tavily_color = '#22c55e' if tracker.tavily_called else '#6b7280'
    amap_icon   = '✅' if tracker.amap_called   else '⬜'
    tavily_icon = '✅' if tracker.tavily_called else '⬜'
    spin = ' <span style="animation:spin 1s linear infinite;display:inline-block">⏳</span>' if running else ''

    details_html = ''
    if tracker.call_details:
        details_html = '<div style="margin-top:10px;font-size:12px;color:#d1d5db">'
        details_html += '<div style="font-weight:600;margin-bottom:4px;color:#9ca3af">调用详情</div>'
        for d in tracker.call_details:
            details_html += (
                f'<div style="background:#1f2937;padding:4px 8px;border-radius:4px;'
                f'margin-bottom:3px;font-family:monospace">'
                f'<span style="color:#60a5fa">{d["server"]}</span> › '
                f'<span style="color:#a3e635">{d["tool"]}</span>'
                f'</div>'
            )
        details_html += '</div>'

    return f"""
<style>
@keyframes spin {{ from{{transform:rotate(0deg)}} to{{transform:rotate(360deg)}} }}
</style>
<div style="background:#111827;border-radius:12px;padding:16px;font-family:sans-serif;color:#f9fafb">
  <div style="font-size:14px;font-weight:700;color:#f9fafb;margin-bottom:12px;
       border-bottom:1px solid #374151;padding-bottom:8px">
    🔌 MCP 工具状态{spin}
  </div>

  <div style="display:flex;flex-direction:column;gap:8px">

    <div style="background:#1f2937;border-radius:8px;padding:10px 12px;
         border-left:3px solid {amap_color}">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:18px">{amap_icon}</span>
        <div>
          <div style="font-size:13px;font-weight:600;color:#f9fafb">高德地图 MCP</div>
          <div style="font-size:11px;color:#9ca3af;margin-top:2px">
            地点查询 · 路线规划 · 周边搜索
          </div>
        </div>
      </div>
    </div>

    <div style="background:#1f2937;border-radius:8px;padding:10px 12px;
         border-left:3px solid {tavily_color}">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:18px">{tavily_icon}</span>
        <div>
          <div style="font-size:13px;font-weight:600;color:#f9fafb">Tavily 搜索 MCP</div>
          <div style="font-size:11px;color:#9ca3af;margin-top:2px">
            行业资讯 · 竞品分析 · 实时新闻
          </div>
        </div>
      </div>
    </div>

  </div>
  {details_html}
</div>
"""


def _empty_fig() -> go.Figure:
    """返回空白占位图"""
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor='#111827',
        plot_bgcolor='#111827',
        font_color='#6b7280',
        annotations=[{
            'text': '查询后图表将在此处显示',
            'xref': 'paper', 'yref': 'paper',
            'x': 0.5, 'y': 0.5,
            'showarrow': False,
            'font': {'size': 14, 'color': '#6b7280'},
        }],
        margin=dict(l=20, r=20, t=20, b=20),
    )
    return fig


# ══════════════════════════════════════════════
# Tab 1：数据分析（Vanna SQL + 图表）
# ══════════════════════════════════════════════

def sql_query(question: str, history: list):
    """
    Vanna Text-to-SQL 查询处理函数。
    流程：generate_sql → run_sql → 本地固定规则图表
    返回：(history, sql_text, table_df, plotly_fig, status_html)
    """
    if not question.strip():
        yield history, '', None, _empty_fig(), _make_status_html(_tracker)
        return

    history = list(history or [])

    # Step 1: 生成 SQL
    history.append({'role': 'user', 'content': question})
    history.append({'role': 'assistant', 'content': '⏳ 正在生成 SQL...'})
    yield history, '', None, _empty_fig(), _make_status_html(_tracker)

    try:
        vn = get_vanna()
        sql = vn.generate_sql(question, allow_llm_to_see_data=allow_data())
    except Exception as e:
        history[-1]['content'] = f'❌ SQL 生成失败：{type(e).__name__}'
        yield history, '', None, _empty_fig(), _make_status_html(_tracker)
        return

    history[-1]['content'] = f'⏳ 正在执行 SQL...'
    yield history, f'```sql\n{sql}\n```', None, _empty_fig(), _make_status_html(_tracker)

    # Step 2: 执行 SQL
    try:
        df = vn.run_sql(sql)
    except Exception as e:
        history[-1]['content'] = f'❌ SQL 执行失败：{type(e).__name__}'
        yield history, f'```sql\n{sql}\n```', None, _empty_fig(), _make_status_html(_tracker)
        return

    if df is None or df.empty:
        history[-1]['content'] = '查询完成，结果为空。'
        yield history, f'```sql\n{sql}\n```', df, _empty_fig(), _make_status_html(_tracker)
        return

    history[-1]['content'] = f'⏳ 正在生成图表（共 {len(df)} 行数据）...'
    yield history, f'```sql\n{sql}\n```', df, _empty_fig(), _make_status_html(_tracker)

    # Step 3: 生成 Plotly 图表
    fig = _empty_fig()
    try:
        fig = safe_chart(df)
        # 统一深色背景
        fig.update_layout(
            paper_bgcolor='#111827',
            plot_bgcolor='#1f2937',
            font_color='#f9fafb',
        )
    except Exception as e:
        print(f'[图表生成异常] {type(e).__name__}')  # 打印但不中断，表格照常展示

    # Step 4: 生成文字摘要
    try:
        summary = vn.generate_summary(question=question, df=df)
        history[-1]['content'] = summary
    except Exception:
        history[-1]['content'] = f'查询完成，共返回 {len(df)} 行数据。'

    yield history, f'```sql\n{sql}\n```', df, fig, _make_status_html(_tracker)


# ══════════════════════════════════════════════
# Tab 2：智能问答（MCP Agent）
# ══════════════════════════════════════════════

def mcp_query(question: str, history: list):
    """
    MCP Agent 查询处理函数（流式）。
    自动检测并展示高德/Tavily 的调用状态。
    """
    if not question.strip():
        yield history, _make_status_html(_tracker)
        return

    history = list(history or [])
    history.append({'role': 'user', 'content': question})
    history.append({'role': 'assistant', 'content': '⏳ 正在思考，可能调用 MCP 工具...'})

    # 初始状态：显示 loading
    yield history, _make_status_html(_tracker, running=True)

    # 流式运行 Agent
    last_text = ''
    for text, tracker in run_mcp_query(question, history[:-2], tracker=_tracker):
        if text:
            last_text = text
            history[-1]['content'] = text
        yield history, _make_status_html(tracker, running=True)

    # 最终状态
    if not last_text:
        history[-1]['content'] = '（未获得回复，请重试）'
    yield history, _make_status_html(_tracker, running=False)


# ══════════════════════════════════════════════
# Gradio UI 构建
# ══════════════════════════════════════════════

CSS = """
/* 全局深色背景 */
body, .gradio-container { background: #0f172a !important; color: #f1f5f9 !important; }
.gradio-container { max-width: 1400px !important; }

/* 标题区 */
.header-box {
    background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
    border-bottom: 1px solid #1e3a5f;
    padding: 18px 24px;
    margin-bottom: 0;
}

/* Tab 样式 */
.tabs > .tab-nav { background: #1e293b !important; border-bottom: 1px solid #334155 !important; }
.tabs > .tab-nav > button { color: #94a3b8 !important; }
.tabs > .tab-nav > button.selected { color: #38bdf8 !important; border-bottom: 2px solid #38bdf8 !important; }

/* 输入框 */
.input-row textarea { background: #1e293b !important; border: 1px solid #334155 !important;
    color: #f1f5f9 !important; border-radius: 8px !important; }

/* 按钮 */
button.primary { background: linear-gradient(135deg,#0369a1,#0ea5e9) !important;
    border: none !important; border-radius: 8px !important; color: white !important; }
button.secondary { background: #1e293b !important; border: 1px solid #334155 !important;
    color: #94a3b8 !important; border-radius: 8px !important; }

/* Chatbot */
.chatbot { background: #111827 !important; border: 1px solid #1e3a5f !important; border-radius: 12px !important; }

/* SQL 代码框 */
.sql-box textarea { background: #0f172a !important; color: #a3e635 !important;
    font-family: 'JetBrains Mono', monospace !important; border: 1px solid #334155 !important; }

/* Dataframe */
.dataframe { background: #111827 !important; color: #f1f5f9 !important; }

/* 快捷建议按钮 */
.suggestion-btn { background: #1e293b !important; border: 1px solid #334155 !important;
    color: #94a3b8 !important; border-radius: 20px !important; font-size: 12px !important;
    padding: 4px 12px !important; cursor: pointer; margin: 3px !important; }
.suggestion-btn:hover { background: #0369a1 !important; color: white !important; }
"""

# 数据分析 Tab 的建议问题
SQL_SUGGESTIONS = [
    '2025年1月各产品的扫码开瓶数量排名',
    '2025年第一季度各省份扫码量对比',
    '2025年上半年每月中奖率趋势',
    '各省份活跃终端客户开瓶数量',
    '2025年第一季度终端客户扫码排名Top20',
    '不同等级终端客户的扫码量分布',
]

# MCP 问答 Tab 的建议问题
MCP_SUGGESTIONS = [
    '帮我搜索2025年白酒行业最新市场动态',
    '泸州老窖在泸州市的总部地址在哪里',
    '搜索茅台与五粮液最新竞争分析',
    '成都市锦江区附近有哪些白酒经销商',
    '查找最近一个月关于白酒涨价的新闻',
    '贵州茅台镇到仁怀市怎么走',
]


def build_ui() -> gr.Blocks:
    """构建完整 Gradio Blocks UI"""

    with gr.Blocks(css=CSS, title='BC白酒营销助手') as demo:

        # ── 标题 ──
        gr.HTML("""
        <div class="header-box">
          <div style="display:flex;align-items:center;gap:12px">
            <span style="font-size:32px">🍶</span>
            <div>
              <div style="font-size:20px;font-weight:700;color:#f1f5f9">BC 白酒营销助手</div>
              <div style="font-size:13px;color:#64748b;margin-top:2px">
                Text-to-SQL 数据分析 &nbsp;·&nbsp; 高德地图 MCP &nbsp;·&nbsp; Tavily 搜索 MCP
              </div>
            </div>
          </div>
        </div>
        """)

        with gr.Tabs():

            # ══════════════════ Tab 1：数据分析 ══════════════════
            with gr.TabItem('📊 数据分析（Text-to-SQL）'):
                with gr.Row():

                    # 左侧主区域
                    with gr.Column(scale=7):
                        chatbot_sql = gr.Chatbot(
                            label='对话',
                            type='messages',
                            height=280,
                            elem_classes=['chatbot'],
                        )
                        with gr.Row(elem_classes=['input-row']):
                            sql_input = gr.Textbox(
                                placeholder='输入数据查询问题，例如：2025年各省份扫码量排名...',
                                show_label=False,
                                scale=8,
                                lines=1,
                            )
                            sql_btn  = gr.Button('查询', variant='primary', scale=1)
                            sql_clear = gr.Button('清空', variant='secondary', scale=1)

                        # 快捷建议
                        gr.HTML('<div style="margin:8px 0 4px;font-size:12px;color:#64748b">快捷问题：</div>')
                        with gr.Row():
                            for s in SQL_SUGGESTIONS:
                                gr.Button(s, elem_classes=['suggestion-btn'], size='sm').click(
                                    fn=lambda x=s: x, outputs=sql_input
                                )

                        # SQL 展示
                        sql_display = gr.Markdown(
                            label='生成的 SQL',
                            value='',
                            elem_classes=['sql-box'],
                        )

                        # 数据表格
                        result_table = gr.Dataframe(
                            label='查询结果',
                            interactive=False,
                            wrap=True,
                        )

                    # 右侧：图表 + MCP 状态
                    with gr.Column(scale=5):
                        result_chart = gr.Plot(
                            label='数据图表',
                            value=_empty_fig(),
                        )
                        mcp_status_sql = gr.HTML(
                            value=_make_status_html(_tracker),
                            label='MCP 状态',
                        )

                # 事件绑定
                sql_btn.click(
                    fn=sql_query,
                    inputs=[sql_input, chatbot_sql],
                    outputs=[chatbot_sql, sql_display, result_table, result_chart, mcp_status_sql],
                )
                sql_input.submit(
                    fn=sql_query,
                    inputs=[sql_input, chatbot_sql],
                    outputs=[chatbot_sql, sql_display, result_table, result_chart, mcp_status_sql],
                )
                sql_clear.click(
                    fn=lambda: ([], '', None, _empty_fig(), _make_status_html(McpCallTracker())),
                    outputs=[chatbot_sql, sql_display, result_table, result_chart, mcp_status_sql],
                )

            # ══════════════════ Tab 2：MCP 智能问答 ══════════════════
            with gr.TabItem('🔌 智能问答（高德 + Tavily MCP）'):
                with gr.Row():

                    # 左侧主区域
                    with gr.Column(scale=7):
                        chatbot_mcp = gr.Chatbot(
                            label='MCP 对话',
                            type='messages',
                            height=400,
                            elem_classes=['chatbot'],
                        )
                        with gr.Row(elem_classes=['input-row']):
                            mcp_input = gr.Textbox(
                                placeholder='输入问题，助手会自动决策是否调用高德/Tavily...',
                                show_label=False,
                                scale=8,
                                lines=1,
                            )
                            mcp_btn   = gr.Button('发送', variant='primary', scale=1)
                            mcp_clear = gr.Button('清空', variant='secondary', scale=1)

                        # 快捷建议
                        gr.HTML('<div style="margin:8px 0 4px;font-size:12px;color:#64748b">快捷问题：</div>')
                        with gr.Row():
                            for s in MCP_SUGGESTIONS:
                                gr.Button(s, elem_classes=['suggestion-btn'], size='sm').click(
                                    fn=lambda x=s: x, outputs=mcp_input
                                )

                    # 右侧：MCP 状态面板
                    with gr.Column(scale=3):
                        gr.HTML("""
                        <div style="padding:12px 0 8px;font-size:13px;font-weight:600;color:#94a3b8">
                          实时 MCP 调用状态
                        </div>
                        """)
                        mcp_status_panel = gr.HTML(
                            value=_make_status_html(_tracker),
                        )
                        gr.HTML("""
                        <div style="margin-top:16px;background:#1e293b;border-radius:8px;padding:12px;
                             font-size:12px;color:#64748b;line-height:1.8">
                          <div style="font-weight:600;color:#94a3b8;margin-bottom:6px">💡 使用提示</div>
                          <div>• 问地图/位置 → 自动调用高德</div>
                          <div>• 问行业资讯 → 自动调用Tavily</div>
                          <div>• 绿色✅ 表示本轮已触发</div>
                          <div>• 支持连续多轮对话</div>
                        </div>
                        """)

                # 事件绑定（流式）
                mcp_btn.click(
                    fn=mcp_query,
                    inputs=[mcp_input, chatbot_mcp],
                    outputs=[chatbot_mcp, mcp_status_panel],
                )
                mcp_input.submit(
                    fn=mcp_query,
                    inputs=[mcp_input, chatbot_mcp],
                    outputs=[chatbot_mcp, mcp_status_panel],
                )
                mcp_clear.click(
                    fn=lambda: ([], _make_status_html(McpCallTracker())),
                    outputs=[chatbot_mcp, mcp_status_panel],
                )

    return demo


# ══════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════

def main() -> None:
    port = int(os.getenv('GRADIO_PORT', '7860'))

    print('=' * 60)
    print('  BC白酒营销助手 — Gradio 统一 UI')
    print(f'  http://127.0.0.1:{port}')
    print('  Tab1: Text-to-SQL 数据分析')
    print('  Tab2: 高德 + Tavily MCP 智能问答')
    print('=' * 60)

    print('\n初始化 Vanna（仅加载 ChromaDB 向量库，数据库连接懒加载）...')
    # 不在启动时连接 MySQL，避免网络抖动导致启动失败
    # 第一次用户查询时自动连接
    print('Vanna 已准备好 ✓')
    print('MCP Agent 懒加载，首次使用时 npx 拉起 MCP server...')

    demo = build_ui()
    demo.launch(
        server_name='127.0.0.1',
        server_port=port,
        share=False,
        show_error=False,
    )


if __name__ == '__main__':
    main()
