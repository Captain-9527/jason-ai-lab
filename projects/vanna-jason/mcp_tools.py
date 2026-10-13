"""
BC白酒营销助手 — MCP 工具调用模块
=====================================
封装高德地图 MCP 和 Tavily 搜索 MCP，通过 qwen-agent Assistant 驱动。

职责：
  - 初始化同时挂载两个 MCP Server 的 Agent
  - 提供 run_mcp_query(question) 同步接口，流式返回每步内容
  - 记录本轮哪些 MCP 被实际调用（用于 UI 状态展示）

MCP 调用流程：
  用户问题 → Assistant.run(messages)
    → LLM 判断是否需要工具
    → 调用 amap-maps / tavily-mcp
    → 返回最终回答
    → 记录调用的工具名

作者：Jason
"""

import os
import threading
from pathlib import Path
from typing import Generator, List

# ── .env 加载（兜底，bc_vanna 已加载过一次）──
_env_path = Path(__file__).parent / '.env'
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _v = _line.split('=', 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

import dashscope
from qwen_agent.agents import Assistant

dashscope.api_key = os.getenv('DASHSCOPE_API_KEY', '')

# ── MCP 工具标识 ──
MCP_AMAP   = 'amap-maps'
MCP_TAVILY = 'tavily-mcp'


# ════════════════════════════════════════════════
# Agent 初始化（含两个 MCP Server）
# ════════════════════════════════════════════════

def _build_agent() -> Assistant:
    """
    构建挂载高德 + Tavily 双 MCP 的 qwen-agent Assistant。

    使用单个 mcpServers dict 同时注册两个 server，
    qwen-agent 会在首次使用时通过 npx 启动对应进程。
    """
    llm_cfg = {
        'model': 'qwen-plus',
        'api_key': os.getenv('DASHSCOPE_API_KEY', ''),
        'timeout': 60,
    }

    system = (
        '你是白酒BC营销助手。除了能查询内部销售数据库外，你还具备：\n'
        '1. 高德地图能力：查询地点、规划路线、搜索周边（适用于查询经销商位置、配送路线等）\n'
        '2. Tavily 网络搜索：搜索白酒行业资讯、竞品信息、市场动态等实时网络内容\n'
        '请根据用户问题判断是否需要使用以上工具，优先使用工具获取准确信息。'
    )

    tools = [{
        'mcpServers': {
            MCP_AMAP: {
                'command': 'npx',
                'args': ['-y', '@amap/amap-maps-mcp-server'],
                'env': {
                    'AMAP_MAPS_API_KEY': os.getenv('AMAP_MAPS_API_KEY', '')
                }
            },
            MCP_TAVILY: {
                'command': 'npx',
                'args': ['-y', 'tavily-mcp@0.1.4'],
                'disabled': False,
                'autoApprove': [],
                'env': {
                    'TAVILY_API_KEY': os.getenv('TAVILY_API_KEY', '')
                }
            }
        }
    }]

    return Assistant(
        llm=llm_cfg,
        name='白酒营销MCP助手',
        description='高德地图 + Tavily 搜索双 MCP 白酒营销助手',
        system_message=system,
        function_list=tools,
    )


# ── 全局单例（线程安全懒加载）──
_agent: Assistant = None
_agent_lock = threading.Lock()


def get_agent() -> Assistant:
    """获取全局 Agent 单例（首次调用时初始化）"""
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = _build_agent()
    return _agent


# ════════════════════════════════════════════════
# 调用追踪器
# ════════════════════════════════════════════════

class McpCallTracker:
    """记录本轮对话中实际触发的 MCP 工具"""

    def __init__(self):
        self.called: List[str] = []     # 已调用的 MCP server 名
        self.call_details: List[dict] = []  # 详细调用记录（tool_name / args）

    def reset(self):
        self.called.clear()
        self.call_details.clear()

    def record(self, server: str, tool_name: str, args: dict = None):
        if server not in self.called:
            self.called.append(server)
        # 同一个工具名只记录一次，避免流式响应重复追加
        already = any(d['tool'] == tool_name for d in self.call_details)
        if not already:
            self.call_details.append({
                'server': server,
                'tool': tool_name,
                'args': args or {}
            })

    @property
    def amap_called(self) -> bool:
        return MCP_AMAP in self.called

    @property
    def tavily_called(self) -> bool:
        return MCP_TAVILY in self.called

    def summary(self) -> str:
        """返回调用摘要文本"""
        if not self.called:
            return '本轮未调用 MCP 工具'
        parts = []
        for detail in self.call_details:
            parts.append(f"[{detail['server']}] {detail['tool']}")
        return '\n'.join(parts)


# ════════════════════════════════════════════════
# 工具名解析辅助
# ════════════════════════════════════════════════

def _detect_mcp_server(tool_full: str, tracker: McpCallTracker) -> None:
    """
    根据工具全名判断属于哪个 MCP server 并记录。

    qwen-agent 0.0.25 实际工具名格式（连字符拼接）：
      amap-maps-maps_text_search
      tavily-mcp-tavily-search
    判断策略：工具名以 server 名称为前缀（忽略大小写）
    """
    t = tool_full.lower()
    if t.startswith('amap-maps') or 'amap' in t:
        tracker.record(MCP_AMAP, tool_full)
    elif t.startswith('tavily') or 'tavily' in t:
        tracker.record(MCP_TAVILY, tool_full)


# ════════════════════════════════════════════════
# 核心查询接口
# ════════════════════════════════════════════════

def run_mcp_query(
    question: str,
    history: list = None,
    tracker: McpCallTracker = None,
) -> Generator[tuple, None, None]:
    """
    执行 MCP Agent 查询，流式 yield (text_chunk, tracker) 元组。

    Args:
        question: 用户问题
        history:  对话历史（list of dict，role/content）
        tracker:  McpCallTracker 实例，用于记录调用情况

    Yields:
        (accumulated_text: str, tracker: McpCallTracker)
        每次 LLM/工具 返回新内容时触发一次

    MCP 工具调用检测原理：
      qwen-agent 返回的 response 列表中，角色为 'tool' 的消息
      content 是工具执行结果，name 字段含有工具名（server__toolname 格式）。
      通过解析工具名前缀来判断调用了哪个 MCP server。
    """
    if tracker is None:
        tracker = McpCallTracker()
    tracker.reset()

    agent = get_agent()
    messages = list(history or [])
    messages.append({'role': 'user', 'content': question})

    accumulated = ''
    last_response = []

    for response in agent.run(messages):
        last_response = response
        # 解析每步响应，检测 MCP 工具调用
        for msg in response:
            role = msg.get('role', '')
            content = msg.get('content', '')
            name = msg.get('name', '')

            # qwen-agent 0.0.25 工具调用消息：
            #   role='function', name='tavily-mcp-tavily-search'（工具执行结果）
            #   role='assistant', function_call={'name': 'tavily-mcp-tavily-search', ...}（工具调用请求）
            # 工具名格式：{server_name}-{tool_function_name}（连字符拼接）
            # 例如：tavily-mcp-tavily-search, amap-maps-maps_search_detail

            if role == 'function':
                # 工具执行结果消息，name 就是工具全名
                tool_full = name or ''
                _detect_mcp_server(tool_full, tracker)

            elif role == 'assistant':
                # 可能含 function_call（工具调用请求）或文本回答
                func_call = msg.get('function_call') or {}
                if func_call:
                    tool_full = func_call.get('name', '')
                    _detect_mcp_server(tool_full, tracker)

                # 累积最终回答文本
                if isinstance(content, str) and content:
                    accumulated = content
                elif isinstance(content, list):
                    text = ' '.join(
                        c.get('text', '') for c in content if isinstance(c, dict)
                    )
                    if text:
                        accumulated = text

        yield accumulated, tracker

    # 最后确保 yield 一次最终状态
    yield accumulated, tracker
