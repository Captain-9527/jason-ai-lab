# Hermes Agent 架构设计深度解析

> **版本**: v0.16.0 | **作者**: Nous Research | **语言**: Python 3.11+
>
> 本文档基于 hermes-agent-main 源码深度分析，面向技术人员，涵盖整体架构、框架设计和核心模块的详细设计。

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [核心入口与启动流程](#3-核心入口与启动流程)
4. [AIAgent 会话循环](#4-aiagent-会话循环)
5. [工具系统](#5-工具系统)
6. [技能系统](#6-技能系统)
7. [内存与记忆管理](#7-内存与记忆管理)
8. [上下文压缩系统](#8-上下文压缩系统)
9. [会话持久化](#9-会话持久化)
10. [错误处理与故障转移](#10-错误处理与故障转移)
11. [凭证管理系统](#11-凭证管理系统)
12. [策展系统](#12-策展系统)
13. [消息网关系统](#13-消息网关系统)
14. [插件系统](#14-插件系统)
15. [定时任务系统](#15-定时任务系统)
16. [CLI 子系统](#16-cli-子系统)
17. [安全设计](#17-安全设计)
18. [关键设计模式总结](#18-关键设计模式总结)

---

## 1. 项目概述

Hermes Agent 是一个**自进化的个人 AI 智能体**，由 Nous Research 开源。它能在 CLI、消息网关（Telegram/Discord/Slack 等 20+ 平台）、TUI 和 Electron 桌面应用中运行相同的 Agent 核心。具备跨会话学习（记忆 + 技能）、子代理委派、定时任务调度、真实终端和浏览器操作能力。

### 1.1 核心设计哲学

| 原则 | 说明 |
| ---- | ---- |
| **提示缓存是神圣的** | 长会话每轮复用缓存前缀，任何中途修改都会使缓存失效、成本倍增 |
| **核心是窄腰，能力在边缘** | 新增功能优先走技能/CLI命令/插件，而非核心工具，因为每个核心工具都会在每次 API 调用时发送 |
| **最小爆炸半径** | 依赖精确锁定（`==X.Y.Z`），不设范围；Provider 特定包延迟安装 |

### 1.2 项目规模

| 指标 | 数量 |
| ---- | ---- |
| Python 模块 | 200+ |
| 测试文件 | ~900 |
| 测试用例 | ~17,000 |
| 消息平台适配器 | 20+ |
| 模型提供商 | 50+ |
| 内置技能 | 50+ |

---

## 2. 整体架构

### 2.1 分层架构图

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         接入层 (Entry Layer)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ CLI 界面  │  │ TUI 界面  │  │ 桌面应用  │  │ ACP 编辑器集成    │   │
│  │ (cli.py)  │  │ (ui-tui/) │  │ (Electron)│  │ (acp_adapter/)   │   │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └────────┬─────────┘   │
│        └───────────────┴─────────────┴─────────────────┘             │
│                                │                                     │
├────────────────────────────────┼─────────────────────────────────────┤
│                         核心层 (Core Layer)                           │
│  ┌─────────────────────────────┼─────────────────────────────────┐   │
│  │                    AIAgent (run_agent.py)                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │   │
│  │  │ 会话循环       │  │ 工具编排      │  │ 提示构建           │  │   │
│  │  │ (conversation │  │ (model_tools │  │ (prompt_builder    │  │   │
│  │  │  _loop.py)    │  │  .py)        │  │  .py)              │  │   │
│  │  └──────────────┘  └──────────────┘  └────────────────────┘  │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │   │
│  │  │ 上下文压缩     │  │ 内存管理      │  │ 错误分类           │  │   │
│  │  │ (context_     │  │ (memory_     │  │ (error_classifier  │  │   │
│  │  │  compressor)  │  │  manager)    │  │  .py)              │  │   │
│  │  └──────────────┘  └──────────────┘  └────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                │                                     │
├────────────────────────────────┼─────────────────────────────────────┤
│                         能力层 (Capability Layer)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ 工具系统       │  │ 技能系统      │  │ 提供商适配器              │   │
│  │ (tools/)      │  │ (skills/)    │  │ (agent/transports/)      │   │
│  │ - 终端操作     │  │ - 开发技能    │  │ - Anthropic Adapter      │   │
│  │ - 文件操作     │  │ - 媒体技能    │  │ - Gemini Adapter         │   │
│  │ - 浏览器控制   │  │ - 研究技能    │  │ - Codex Adapter          │   │
│  │ - Web 搜索     │  │ - AI 代理技能 │  │ - Bedrock Adapter        │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
│                                │                                     │
├────────────────────────────────┼─────────────────────────────────────┤
│                         基础设施层 (Infrastructure)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ 会话持久化     │  │ 插件系统      │  │ 日志系统                  │   │
│  │ (hermes_     │  │ (plugins/)   │  │ (hermes_logging.py)      │   │
│  │  state.py)   │  │              │  │                          │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ 定时任务       │  │ 消息网关      │  │ 凭证管理                  │   │
│  │ (cron/)       │  │ (gateway/)   │  │ (credential_pool.py)     │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构

```text
hermes-agent/
├── run_agent.py              # AIAgent 类 — 核心会话循环 (~5300行)
├── model_tools.py            # 工具编排层 — 发现、过滤、分发工具 (~1230行)
├── toolsets.py               # 工具集定义 — 工具分组与递归解析 (~850行)
├── cli.py                    # HermesCLI — 交互式 CLI 编排器 (~11000行)
├── hermes_state.py           # SessionDB — SQLite 会话存储 + FTS5 搜索 (~3600行)
├── hermes_constants.py       # get_hermes_home() — 配置文件路径解析
├── hermes_logging.py         # setup_logging() — 日志子系统配置
├── trajectory_compressor.py  # 轨迹压缩 — 训练数据 token 预算控制 (~1400行)
├── batch_runner.py           # 并行批处理
├── hermes_bootstrap.py       # Windows UTF-8 引导
│
├── agent/                    # Agent 内部模块 (115+ 文件)
│   ├── conversation_loop.py  # 会话循环实现 (~3900行)
│   ├── context_compressor.py # 上下文自动压缩
│   ├── memory_manager.py     # 记忆提供商编排
│   ├── prompt_builder.py     # 系统提示构建
│   ├── error_classifier.py   # API 错误分类与故障转移
│   ├── credential_pool.py    # 多凭证池管理
│   ├── curator.py            # 技能自动维护
│   ├── tool_executor.py      # 工具调用执行引擎
│   ├── tool_guardrails.py    # 工具循环防护
│   ├── transports/           # 提供商传输层
│   ├── lsp/                  # Language Server Protocol 集成
│   └── ...
│
├── tools/                    # 工具实现 (~90 文件)
│   ├── registry.py           # 工具注册表 (自注册模式)
│   ├── terminal_tool.py      # 终端工具
│   ├── file_tools.py         # 文件操作工具
│   ├── browser_tool.py       # 浏览器自动化
│   ├── environments/         # 执行环境后端
│   │   ├── local.py          # 本地执行
│   │   ├── docker.py         # Docker 容器
│   │   ├── ssh.py            # SSH 远程
│   │   ├── modal.py          # Modal 云沙箱
│   │   └── ...
│   └── ...
│
├── gateway/                  # 消息网关
│   ├── run.py                # 网关启动器 (~800KB!)
│   ├── session.py            # 会话管理
│   ├── config.py             # 配置管理
│   ├── platforms/            # 平台适配器 (20+)
│   │   ├── base.py           # 基础适配器接口
│   │   ├── telegram.py       # Telegram
│   │   ├── discord.py        # Discord
│   │   ├── slack.py          # Slack
│   │   ├── whatsapp.py       # WhatsApp
│   │   └── ...
│   └── hooks.py              # 事件钩子系统
│
├── plugins/                  # 插件系统 (20+ 插件)
│   ├── memory/               # 记忆后端 (Honcho, Mem0, ...)
│   ├── model-providers/      # 模型提供商 (50+)
│   ├── image_gen/            # 图像生成
│   ├── browser/              # 浏览器提供商
│   └── ...
│
├── skills/                   # 内置技能 (50+)
├── hermes_cli/               # CLI 子系统
├── cron/                     # 定时任务系统
├── acp_adapter/              # 编辑器集成 (VS Code / Zed / JetBrains)
├── ui-tui/                   # Ink (React) 终端 UI
├── tests/                    # 测试套件 (~900文件, ~17000测试)
└── docs/                     # 文档
```

### 2.3 核心数据流

```text
用户输入 (CLI/Gateway/ACP)
    │
    ▼
┌──────────────────────────────────────────────┐
│              AIAgent.run_conversation()       │
│                                              │
│  1. 构建系统提示 (prompt_builder)             │
│     ├─ 身份声明                               │
│     ├─ 技能索引注入                            │
│     ├─ 上下文文件扫描 (AGENTS.md/.cursorrules) │
│     └─ 记忆上下文块                            │
│                                              │
│  2. 获取工具定义 (model_tools)                 │
│     ├─ 解析工具集 (toolsets)                   │
│     ├─ 过滤可用工具 (check_fn)                  │
│     └─ 返回 OpenAI 格式 schema                │
│                                              │
│  3. 模型 API 调用 (OpenAI SDK)                 │
│     ├─ 带提示缓存控制                          │
│     ├─ 多凭证池故障转移                         │
│     └─ 指数退避重试                            │
│                                              │
│  4. 工具调用处理 (循环)                        │
│     ├─ 参数类型强制转换                         │
│     ├─ 工具守卫检查                            │
│     ├─ 前置/后置钩子                           │
│     ├─ 工具执行 (顺序/并发)                     │
│     └─ 结果分类与持久化                         │
│                                              │
│  5. 后轮处理                                  │
│     ├─ 记忆同步 (sync_turn)                    │
│     ├─ 上下文压缩检查                           │
│     └─ 后台技能审查触发                         │
│                                              │
│  6. 保存到 SessionDB                          │
└──────────────────────────────────────────────┘
    │
    ▼
输出交付 (CLI显示 / Gateway推送 / ACP响应)
```

---

## 3. 核心入口与启动流程

### 3.1 启动入口

```python
# hermes (可执行脚本)
#!/usr/bin/env python3
from hermes_cli.main import main
main()
```

### 3.2 引导过程

```text
hermes 命令
    │
    ├─ hermes_bootstrap.apply_windows_utf8_bootstrap()
    │   └─ Windows: 设置 PYTHONUTF8=1, 重配置 stdio 为 UTF-8
    │
    ├─ hermes_cli.main.main()
    │   ├─ 解析命令行参数
    │   ├─ 加载 .env (API 密钥)
    │   ├─ 加载 config.yaml (行为配置)
    │   └─ 分发到子命令
    │       ├─ cmd_chat()    → 交互式聊天
    │       ├─ cmd_gateway() → 启动消息网关
    │       ├─ cmd_setup()   → 交互式设置向导
    │       └─ cmd_model()   → 模型选择
    │
    └─ 创建 AIAgent 实例
        ├─ agent.agent_init.init_agent()
        ├─ 加载工具定义
        ├─ 初始化 SessionDB
        └─ 配置模型参数
```

### 3.3 AIAgent 初始化参数

AIAgent 构造函数接收 **40+ 个参数**，核心参数包括：

| 参数 | 类型 | 说明 |
| ---- | ---- | ---- |
| `base_url` | str | 模型 API 端点 |
| `model` | str | 模型名称 |
| `max_iterations` | int (默认 90) | 工具调用最大轮数 |
| `enabled_toolsets` | List[str] | 启用的工具集 |
| `disabled_toolsets` | List[str] | 禁用的工具集 |
| `skills` | List[str] | 预加载技能 |
| `system_message` | str | 自定义系统提示 |
| `session_id` | str | 会话 ID（恢复用） |
| `quiet_mode` | bool | 静默模式 |
| `approval_callback` | Callable | 批准回调函数 |

---

## 4. AIAgent 会话循环

### 4.1 架构设计

`run_agent.py` 中的 `AIAgent` 类是门面（Facade），实际的会话循环逻辑已提取到 `agent/conversation_loop.py`：

```python
# run_agent.py — 门面层
class AIAgent:
    def run_conversation(self, user_message, ...):
        # 委托给 conversation_loop
        return agent.conversation_loop.run_conversation(self, user_message, ...)
```

### 4.2 会话循环详细流程

```text
run_conversation(agent, user_message)
    │
    ├─ 1. 轮次上下文构建 (build_turn_context)
    │     └─ TurnContext { task_id, turn_start, guardrail_controller, ... }
    │
    ├─ 2. 消息准备
    │     ├─ 追加用户消息到 messages 列表
    │     ├─ 消息清理 (sanitize: 非ASCII, surrogate, 图片剥离)
    │     └─ 工具参数修复 (repair_tool_call_arguments)
    │
    ├─ 3. 工具定义获取
    │     └─ model_tools.get_tool_definitions(enabled_toolsets, disabled_toolsets)
    │
    ├─ 4. 系统提示构建 (如需要)
    │     └─ _build_system_prompt()
    │         ├─ agent.prompt_builder 组装各部分
    │         └─ 注入记忆上下文块 (build_memory_context_block)
    │
    ├─ 5. 主循环 (最多 max_iterations 轮)
    │     │
    │     ├─ 5.1 提示缓存控制
    │     │     └─ apply_anthropic_cache_control(messages)
    │     │         └─ "system_and_3" 策略: 系统提示 + 最后 3 条消息
    │     │
    │     ├─ 5.2 API 调用
    │     │     ├─ OpenAI SDK 调用
    │     │     ├─ 带超时控制
    │     │     └─ 流式响应处理
    │     │
    │     ├─ 5.3 错误处理 (发生错误时)
    │     │     ├─ classify_api_error() → FailoverReason
    │     │     ├─ auth/billing → 凭证轮换
    │     │     ├─ rate_limit → 退避 + 轮换
    │     │     ├─ context_overflow → 压缩上下文
    │     │     ├─ server_error → 重试
    │     │     └─ content_policy → 中止
    │     │
    │     ├─ 5.4 响应处理
    │     │     ├─ 有工具调用 → 进入工具执行流程
    │     │     │     ├─ 顺序执行: _execute_tool_calls_sequential()
    │     │     │     └─ 并发执行: _execute_tool_calls_concurrent()
    │     │     │           └─ 最多 8 个工作线程
    │     │     └─ 无工具调用 → 返回最终响应
    │     │
    │     └─ 5.5 工具执行后处理
    │           ├─ 工具结果持久化
    │           └─ 继续循环
    │
    ├─ 6. 后轮处理
    │     ├─ 记忆同步: memory_manager.sync_turn()
    │     ├─ 上下文压缩检查: context_compressor.should_compress()
    │     └─ 后台技能审查: maybe_run_curator()
    │
    └─ 7. 保存到 SessionDB
```

### 4.3 轮次预算控制

```python
class IterationBudget:
    """控制每轮的迭代次数和令牌消耗"""
    
    max_iterations: int = 90       # 最大工具调用轮数
    max_tokens_per_turn: int       # 每轮最大令牌数
    current_iteration: int = 0     # 当前迭代计数
    
    def can_continue(self) -> bool:
        """检查是否可以继续迭代"""
```

---

## 5. 工具系统

### 5.1 设计理念

工具系统是 Hermes 的**能力提供层**，采用**自注册 + 中心注册表**模式：

- 每个工具文件在模块级别调用 `registry.register()` 自注册
- `model_tools.py` 触发发现过程（导入所有工具模块）
- 注册表提供统一查询和分发接口

### 5.2 工具注册表

```python
# tools/registry.py

class ToolEntry:
    """单个工具的元数据"""
    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars", "dynamic_schema_overrides",
    )

class ToolRegistry:
    """全局工具注册表"""
    
    def register(self, name, toolset, schema, handler, 
                 check_fn=None, requires_env=None, is_async=False,
                 description="", emoji="", override=False):
        """注册一个工具"""
    
    def get_definitions(self, tool_names, quiet_mode=False):
        """获取 OpenAI 格式的工具定义列表"""
    
    def dispatch(self, name, args, task_id=None, ...):
        """执行工具调用"""
```

### 5.3 工具自注册示例

```python
# tools/terminal_tool.py — 模块级别注册

from tools.registry import registry

TERMINAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "在终端中执行 shell 命令",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {"type": "integer", "description": "超时(毫秒)"}
            },
            "required": ["command"]
        }
    }
}

def _handle_terminal(command, timeout=None, task_id=None):
    """终端工具处理函数"""
    # ... 实现

registry.register(
    name="terminal",
    toolset="terminal",
    schema=TERMINAL_SCHEMA,
    handler=_handle_terminal,
    check_fn=check_terminal_requirements,
    emoji="💻",
    max_result_size_chars=100_000,
)
```

### 5.4 工具自动发现

```python
def discover_builtin_tools(tools_dir=None):
    """扫描 tools/ 目录，导入所有自注册工具模块"""
    tools_path = Path(tools_dir) or Path(__file__).resolve().parent
    
    # 使用 AST 解析检查模块是否调用了 registry.register()
    module_names = [
        f"tools.{path.stem}"
        for path in sorted(tools_path.glob("*.py"))
        if path.name not in {"__init__.py", "registry.py", "mcp_tool.py"}
        and _module_registers_tools(path)  # AST 检查
    ]
    
    for mod_name in module_names:
        importlib.import_module(mod_name)  # 导入即注册
```

### 5.5 工具可用性检查

```python
# 30 秒 TTL 缓存，避免重复探测外部状态
_CHECK_FN_TTL_SECONDS = 30.0

def _check_fn_cached(fn):
    """缓存 check_fn 结果"""
    # 适用于: Docker 守护进程、Playwright 二进制、Modal SDK 等
```

### 5.6 工具分类与防护

```python
# 幂等工具 (可安全重试)
IDEMPOTENT_TOOL_NAMES = {
    "read_file", "search_files", "web_search", "vision_analyze",
    "skills_list", "skill_view", "browser_snapshot", ...
}

# 变更工具 (需要谨慎)
MUTATING_TOOL_NAMES = {
    "terminal", "write_file", "patch", "memory", "delegate_task", ...
}

# 循环防护阈值
ToolCallGuardrailConfig(
    exact_failure_warn_after=2,        # 相同失败 2 次后警告
    same_tool_failure_halt_after=8,    # 同类失败 8 次后终止
    no_progress_block_after=5,         # 5 轮无进展后阻塞
)
```

### 5.7 执行环境后端

支持 **7 种**执行环境，统一接口：

```python
class ExecutionEnvironment(ABC):
    """所有执行环境的基类"""
    
    @abstractmethod
    async def execute(self, command: str) -> ExecutionResult:
        """执行命令"""
    
    @abstractmethod
    async def cleanup(self):
        """清理资源"""

# 具体实现
# - local:      本地机器 (最快)
# - docker:     Docker 容器 (隔离)
# - ssh:        SSH 远程主机
# - modal:      Modal 云沙箱
# - daytona:    Daytona 开发环境
# - singularity: Singularity 容器
```

### 5.8 核心工具清单

```python
_HERMES_CORE_TOOLS = [
    # Web 搜索
    "web_search", "web_extract",
    # 终端
    "terminal", "process", "read_terminal",
    # 文件
    "read_file", "write_file", "patch", "search_files",
    # 视觉
    "vision_analyze", "image_generate",
    # 浏览器
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back",
    "browser_press", "browser_get_images", "browser_vision",
    "browser_console", "browser_cdp", "browser_dialog",
    # TTS
    "text_to_speech",
    # 规划 & 记忆
    "todo", "memory",
    # 会话搜索
    "session_search",
    # 代码执行
    "execute_code", "delegate_task",
    # 消息
    "send_message",
    # 智能家居 (Home Assistant)
    "ha_list_entities", "ha_get_state", "ha_call_service",
    # 看板
    "kanban_show", "kanban_create", "kanban_complete", ...
]
```

### 5.9 异步桥接机制

工具系统需要同时支持同步和异步工具处理器，`model_tools.py` 提供了统一的异步桥接：

```python
def _run_async(coro):
    """统一的异步执行入口"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        # 已在异步上下文 (网关) → 新线程 + 新循环
        with ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=timeout)
    else:
        # 同步上下文 (CLI) → 使用持久化循环
        tool_loop = _get_tool_loop()
        return tool_loop.run_until_complete(coro)
```

---

## 6. 技能系统

### 6.1 设计理念

技能是 Hermes 的**知识注入机制**，每个技能是一组 Markdown 文档，通过系统提示注入到 Agent 上下文中，教会 Agent 如何完成特定任务。

### 6.2 技能定义格式

```yaml
---
name: gif-search
description: "通过 Tenor API 搜索和下载 GIF"
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [TENOR_API_KEY]
  commands: [curl, jq]
metadata:
  hermes:
    tags: [GIF, Media, Search]
    related_skills: []
---

# GIF 搜索技能

## 使用方法
使用 Tenor API 搜索 GIF...
```

### 6.3 技能分类

```text
skills/
├── software-development/       # 软件开发
│   ├── plan/                   # 规划 — 生成可执行实现计划
│   ├── spike/                  # 探索 — 快速原型验证
│   ├── test-driven-development/ # TDD — 测试驱动开发
│   └── ...
├── autonomous-ai-agents/       # AI 代理
│   ├── hermes-agent/           # Hermes 自身配置
│   └── opencode/               # 代码生成
├── media/                      # 媒体处理
│   ├── gif-search/
│   └── youtube-content/
├── mlops/                      # MLOps
├── research/                   # 研究
└── ... (50+ 技能)
```

### 6.4 技能加载流程

```text
技能加载
    │
    ├─ 扫描目录
    │   ├─ <repo>/skills/           # 内置技能
    │   ├─ ~/.hermes/skills/        # 用户技能
    │   └─ ~/.hermes/skill-bundles/ # 技能包
    │
    ├─ 解析 SKILL.md
    │   ├─ YAML 前置元数据
    │   └─ Markdown 正文
    │
    ├─ 环境匹配
    │   ├─ 平台匹配 (linux/macos/windows)
    │   ├─ 命令可用性检查
    │   └─ 环境变量检查
    │
    └─ 索引缓存
        └─ skills/index-cache/  (加速后续加载)
```

### 6.5 技能包

技能包允许将多个技能组合使用：

```yaml
# ~/.hermes/skill-bundles/backend-dev.yaml
name: backend-dev
description: Backend feature work
skills:
  - github-code-review
  - test-driven-development
  - plan
instruction: "Focus on clean architecture and test coverage"
```

---

## 7. 内存与记忆管理

### 7.1 架构设计

记忆系统采用**提供商抽象**模式，一次只激活一个外部记忆提供商：

```python
class MemoryProvider(ABC):
    """记忆提供商抽象基类"""
    
    def initialize(self, session_id: str, **kwargs):
        """初始化记忆提供商"""
    
    def system_prompt_block(self) -> str:
        """返回系统提示中的记忆块"""
    
    def prefetch(self, query: str) -> str:
        """每轮对话前预取相关记忆"""
    
    def sync_turn(self, user_msg: str, assistant_response: str):
        """每轮对话后同步记忆"""
    
    def get_tool_schemas(self) -> List[Dict]:
        """返回记忆相关工具定义"""
    
    def handle_tool_call(self, tool_name: str, args: Dict) -> str:
        """处理记忆工具调用"""
    
    def shutdown(self):
        """清理资源"""
```

### 7.2 支持的记忆提供商

| 提供商 | 说明 |
| ---- | ---- |
| Honcho | AI-native 记忆 |
| Mem0 | 向量化记忆存储 |
| Holographic | 本地 SQLite 记忆 |
| SuperMemory | 超级记忆 |
| Hindsight | 事后分析记忆 |

### 7.3 记忆生命周期

```text
会话开始
    │
    ├─ initialize(session_id)
    │     └─ 连接记忆后端
    │
    ├─ system_prompt_block()
    │     └─ 注入系统提示
    │
    ├─ 每轮循环
    │     ├─ prefetch(query)         # 预取相关记忆
    │     │     └─ 搜索结果注入上下文
    │     ├─ [Agent 处理]
    │     └─ sync_turn(user, assistant)  # 同步新记忆
    │
    └─ 会话结束
          └─ shutdown()
```

---

## 8. 上下文压缩系统

### 8.1 设计目标

当对话历史超过模型上下文窗口的 **75%** 时，自动压缩上下文，保持 Agent 继续工作而不丢失关键信息。

### 8.2 压缩策略

```text
原始消息列表 (token 超限)
    │
    ├─ 保护区域 (不压缩)
    │   ├─ protect_first_n = 3    # 系统提示 + 前 3 条消息
    │   └─ protect_last_n = 6     # 最后 6 条消息
    │
    ├─ 可压缩区域 (中间部分)
    │   ├─ 工具输出修剪 (廉价预处理)
    │   │     └─ 大文件输出截断到摘要
    │   ├─ 迭代式总结
    │   │     └─ 保留跨多次压缩的关键信息
    │   └─ 令牌预算尾部保护
    │
    └─ 压缩后格式
          └─ [CONTEXT COMPACTION — REFERENCE ONLY] <摘要内容>
```

### 8.3 压缩配置

```python
ContextCompressor:
    threshold_percent: float = 0.75    # 触发压缩的阈值
    protect_first_n: int = 3           # 保护前 N 条消息
    protect_last_n: int = 6            # 保护后 N 条消息
    summary_budget_scale: float = 1.0  # 摘要预算缩放
```

### 8.4 轨迹压缩

`trajectory_compressor.py` 是训练数据专用的压缩器：

```python
class TrajectoryCompressor:
    """压缩 Agent 轨迹到目标 token 预算"""
    
    def compress_trajectory(self, trajectory):
        """
        核心算法:
        1. 计算每个 turn 的 token 数
        2. 如果总 token ≤ 目标，跳过
        3. 保护头部 (system + 首个 user + 首个 assistant + 首个 tool)
        4. 保护尾部 (最后 N 个 turn)
        5. 压缩中间区域 → 用 LLM 生成摘要替换
        """
    
    def _generate_summary(self, content):
        """使用 OpenRouter API 生成摘要"""
```

---

## 9. 会话持久化

### 9.1 SessionDB 架构

使用 SQLite **WAL 模式 + FTS5 全文搜索**：

```text
~/.hermes/state.db
    │
    ├─ sessions 表
    │   ├─ id (PK)               # UUID
    │   ├─ title                 # 用户设置标题
    │   ├─ started_at            # 创建时间
    │   ├─ ended_at              # 结束时间
    │   ├─ model                 # 使用的模型
    │   ├─ model_config (JSON)   # 模型配置
    │   ├─ parent_session_id     # 父会话 (分支/压缩)
    │   ├─ end_reason            # 结束原因
    │   ├─ source                # 来源 (cli/telegram/...)
    │   └─ cwd                   # 工作目录
    │
    ├─ messages 表
    │   ├─ id (PK)               # UUID
    │   ├─ session_id (FK)       # 所属会话
    │   ├─ role                  # user/assistant/system/tool
    │   ├─ content               # 消息内容
    │   ├─ created_at            # 创建时间
    │   └─ metadata (JSON)       # 元数据
    │
    └─ messages_fts (FTS5 虚拟表)
        └─ 全文搜索索引 (content 列)
```

### 9.2 并发控制

```python
class SessionDB:
    _WRITE_MAX_RETRIES = 15           # 写入重试次数
    _WRITE_RETRY_MIN_S = 0.020        # 最小重试延迟
    _WRITE_RETRY_MAX_S = 0.150        # 最大重试延迟
    _CHECKPOINT_EVERY_N_WRITES = 50   # 检查点频率
    
    # WAL 模式优势:
    # - 多读并发
    # - 单写 (互斥锁)
    # - 读写不互阻
    
    # 网络文件系统自动降级:
    # WAL 失败 → DELETE 日志模式 (兼容 NFS/SMB)
```

### 9.3 会话链式结构

```text
原始会话 (id=A)
    ├─ 分支 1 (parent_session_id=A, end_reason='branched')
    │     └─ 保持可见
    ├─ 分支 2 (parent_session_id=A, end_reason='branched')
    │     └─ 保持可见
    └─ 压缩续集 (parent_session_id=A, end_reason='compression')
          └─ 隐藏 (仅用于延续对话)
```

### 9.4 全文搜索

```python
def search_sessions(self, query: str, limit: int = 20):
    """使用 FTS5 全文搜索会话"""
    # 支持: 模糊搜索、短语搜索、前缀匹配
```

---

## 10. 错误处理与故障转移

### 10.1 错误分类体系

```python
class FailoverReason(Enum):
    auth = "auth"                           # 401/403 → 刷新凭证
    auth_permanent = "auth_permanent"       # 永久失效 → 中止
    billing = "billing"                     # 402 → 立即轮换凭证
    rate_limit = "rate_limit"               # 429 → 退避 + 轮换
    overloaded = "overloaded"               # 503/529 → 退避重试
    server_error = "server_error"           # 500/502 → 重试
    timeout = "timeout"                     # 超时 → 重建连接 + 重试
    context_overflow = "context_overflow"   # 上下文溢出 → 压缩
    model_not_found = "model_not_found"     # 404 → 回退模型
    content_policy_blocked = "content_policy_blocked"  # 内容过滤 → 中止
    format_error = "format_error"           # 400 → 剥离或中止
```

### 10.2 故障转移决策树

```text
API 调用失败
    │
    ├─ 401/403 → FailoverReason.auth
    │     ├─ 可刷新 → 刷新凭证 → 重试
    │     └─ 不可刷新 → auth_permanent → 中止
    │
    ├─ 402 → FailoverReason.billing
    │     └─ 立即轮换凭证 → 重试
    │
    ├─ 429 → FailoverReason.rate_limit
    │     ├─ 指数退避 (jittered_backoff)
    │     └─ 轮换凭证 → 重试
    │
    ├─ 503/529 → FailoverReason.overloaded
    │     └─ 指数退避 → 重试
    │
    ├─ 500/502 → FailoverReason.server_error
    │     └─ 重试 (最多 N 次)
    │
    ├─ 上下文溢出 → FailoverReason.context_overflow
    │     └─ 压缩上下文 (不故障转移到其他凭证)
    │
    ├─ 404 → FailoverReason.model_not_found
    │     └─ 回退到备选模型
    │
    └─ 内容过滤 → FailoverReason.content_policy_blocked
          └─ 中止 (确定性错误，重试无意义)
```

### 10.3 重试策略

```python
def jittered_backoff(attempt: int, base: float = 1.0, max_delay: float = 60.0):
    """带随机抖动的指数退避"""
    delay = min(base * (2 ** attempt), max_delay)
    jitter = random.uniform(0, delay * 0.5)
    return delay + jitter
```

---

## 11. 凭证管理系统

### 11.1 设计理念

支持**多凭证池**，实现自动故障转移和负载均衡：

```python
class CredentialPool:
    """管理多个同类型凭证"""
    
    strategies = {
        "fill_first":     "先用满第一个",
        "round_robin":    "轮询使用",
        "random":         "随机选择",
        "least_used":     "最少使用优先",
    }

class Credential:
    status: str  # ok | exhausted | dead
    
    # 故障转移触发条件:
    # - 402 (billing) → 立即轮换
    # - 429 (rate_limit) → 退避后轮换
    # - 401/403 (auth) → 刷新或轮换
```

### 11.2 凭证源

```python
class CredentialSource(ABC):
    """凭证来源抽象"""
    
    # 内置来源:
    # - 环境变量
    # - .env 文件
    # - Bitwarden Secrets Manager
    # - 自定义插件
```

---

## 12. 策展系统

### 12.1 职责

策展系统 (Curator) 负责**技能的自动维护**：

- 自动转换技能生命周期状态
- 生成后台审查代理评估技能质量
- 持久化策展状态
- 仅触碰 Agent 自身创建的技能
- **从不自动删除**（仅归档）

### 12.2 调度配置

```python
DEFAULT_INTERVAL_HOURS = 24 * 7    # 每 7 天运行一次
DEFAULT_MIN_IDLE_HOURS = 2         # 最少空闲 2 小时
DEFAULT_STALE_AFTER_DAYS = 30      # 30 天未使用标记为过时
DEFAULT_ARCHIVE_AFTER_DAYS = 90    # 90 天后归档
```

---

## 13. 消息网关系统

### 13.1 架构设计

消息网关使 Hermes Agent 能够通过 **20+ 个消息平台** 与用户交互：

```text
gateway/run.py (启动器)
    │
    ├─ 平台适配器 (Platform Adapters)
    │   ├─ BasePlatformAdapter (ABC)
    │   │     ├─ connect() / disconnect()
    │   │     ├─ send() / receive()
    │   │     └─ 能力标志 (supports_code_blocks, typed_command_prefix, ...)
    │   │
    │   ├─ telegram.py    # Telegram Bot
    │   ├─ discord.py     # Discord Bot
    │   ├─ slack.py       # Slack App
    │   ├─ whatsapp.py    # WhatsApp Business
    │   ├─ wecom.py       # 企业微信
    │   ├─ feishu.py      # 飞书
    │   ├─ qqbot/         # QQ 机器人
    │   ├─ signal.py      # Signal
    │   ├─ matrix.py      # Matrix
    │   ├─ email.py       # Email
    │   ├─ sms.py         # SMS
    │   └─ ... (20+)
    │
    ├─ 会话管理 (session.py)
    │   └─ SessionSource { platform, chat_id, user_id, thread_id, ... }
    │
    ├─ 事件钩子 (hooks.py)
    │   └─ gateway:startup, session:start, agent:step, agent:end, ...
    │
    ├─ 消息投递 (delivery.py)
    │   └─ 路由到正确的平台适配器
    │
    └─ 斜杠命令 (slash_commands.py)
        └─ 跨平台统一的命令系统
```

### 13.2 平台适配器接口

```python
class BasePlatformAdapter(ABC):
    """所有平台适配器的基类"""
    
    @abstractmethod
    async def connect(self) -> bool:
        """连接到平台"""
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
    
    @abstractmethod
    async def send(self, chat_id: str, content: str, **kwargs) -> SendResult:
        """发送消息"""
    
    # 能力标志
    supports_code_blocks: bool = True
    supports_markdown: bool = True
    typed_command_prefix: str = "/"
```

### 13.3 会话隔离

每个 `(platform, chat_id, thread_id)` 组合是独立会话，支持 PII 保护：

```python
@dataclass
class SessionSource:
    platform: Platform          # 平台枚举
    chat_id: str                # 聊天 ID
    chat_type: str              # dm / group / channel / thread
    user_id: Optional[str]      # 用户 ID
    user_name: Optional[str]    # 用户名
    thread_id: Optional[str]    # 线程 ID
    role_authorized: bool       # 角色授权标志
```

### 13.4 Webhook 安全工具集

对来自不信任源（如公开 PR 标题/评论）的 Webhook 事件，使用受限工具集：

```python
_HERMES_WEBHOOK_SAFE_TOOLS = [
    "web_search",       # 只读 Web 搜索
    "web_extract",      # 只读 Web 提取
    "vision_analyze",   # 只读视觉分析
    "clarify",          # 澄清问题
]
# 无终端、文件写入、浏览器等危险工具
```

---

## 14. 插件系统

### 14.1 四层发现机制

插件按优先级从低到高从四个来源加载（后来源覆盖前来源）：

```text
1. 捆绑插件:  <repo>/plugins/<name>/     (随代码库发布)
2. 用户插件:  ~/.hermes/plugins/<name>/  (用户自定义)
3. 项目插件:  ./.hermes/plugins/<name>/  (项目级, 需 opt-in)
4. Pip 插件:  hermes_agent.plugins 入口点
```

### 14.2 插件清单

```yaml
# plugin.yaml
name: honcho
version: 1.0.0
description: "Honcho AI-native memory"
pip_dependencies:
  - honcho-ai
hooks:
  - on_session_end
kind: memory
requires_env:
  - HONCHO_API_KEY
```

### 14.3 PluginContext API

```python
class PluginContext:
    """提供给插件的 API 门面 — 单一入口点"""
    
    def register_tool(self, name, toolset, schema, handler, ...):
        """注册新工具"""
    
    def register_platform(self, name, label, adapter_factory, ...):
        """注册新平台适配器"""
    
    def register_memory_provider(self, provider):
        """注册记忆提供商"""
    
    def register_model_provider(self, provider):
        """注册 LLM 提供商"""
    
    def register_command(self, name, handler, description):
        """注册斜杠命令"""
    
    def register_hook(self, event_name, handler):
        """订阅事件钩子"""
```

### 14.4 支持的钩子事件

```python
VALID_HOOKS = {
    # 工具相关
    "pre_tool_call",           # 工具调用前
    "post_tool_call",          # 工具调用后
    "transform_tool_result",   # 转换工具结果
    
    # LLM 相关
    "pre_llm_call",            # LLM 调用前
    "post_llm_call",           # LLM 调用后
    "transform_llm_output",    # 转换 LLM 输出
    
    # 会话相关
    "on_session_start",        # 会话开始
    "on_session_end",          # 会话结束
    "on_session_reset",        # 会话重置
    
    # 网关相关
    "pre_gateway_dispatch",    # 网关分发前
    
    # 批准相关
    "pre_approval_request",    # 批准请求前
    "post_approval_response",  # 批准响应后
}
```

### 14.5 插件分类

| 类别 | 目录 | 示例 |
| ---- | ---- | ---- |
| 记忆后端 | `plugins/memory/` | Honcho, Mem0, SuperMemory |
| 模型提供商 | `plugins/model-providers/` | OpenAI, Anthropic, DeepSeek, Gemini (50+) |
| 图像生成 | `plugins/image_gen/` | FAL, OpenAI, Replicate |
| 浏览器 | `plugins/browser/` | Browserbase, Firecrawl, Browser Use |
| 平台适配 | `plugins/platforms/` | Discord, IRC, HomeAssistant |
| 上下文引擎 | `plugins/context_engine/` | LCM, 自定义引擎 |
| 看板 | `plugins/kanban/` | 多 Agent 任务分发 |
| 可观测性 | `plugins/observability/` | Langfuse 集成 |

---

## 15. 定时任务系统

### 15.1 存储结构

```text
~/.hermes/cron/
├── jobs.json              # 所有定时任务定义
├── output/
│   └── {job_id}/
│       └── {timestamp}.md # 任务执行输出
├── .jobs.lock             # 跨进程互斥锁 (fcntl/msvcrt)
└── .tick.lock             # 调度器 tick 锁
```

### 15.2 任务定义

```json
{
    "id": "uuid",
    "name": "Daily Report",
    "prompt": "Generate today's summary report",
    "schedule": {
        "kind": "cron",           // cron | interval | once
        "expr": "0 9 * * *"       // Cron 表达式
    },
    "skills": ["plan"],
    "enabled_toolsets": ["web", "file"],
    "deliver": "telegram:home",   // 交付目标
    "enabled": true,
    "origin": {
        "platform": "telegram",
        "chat_id": "123456"
    }
}
```

### 15.3 调度器

```python
def tick(verbose=True, adapters=None, loop=None, sync=True) -> int:
    """
    每 60 秒由网关后台线程调用
    返回运行的任务数
    """

# 并行池: 最多 4 个任务同时运行
_parallel_pool = ThreadPoolExecutor(max_workers=4)

# 顺序池: 环境变量修改的任务必须串行
_sequential_pool = ThreadPoolExecutor(max_workers=1)
```

### 15.4 安全机制

1. **提示注入扫描**: 扫描组装后的完整提示词（包括加载的技能内容）
2. **工具集限制**: 禁用交互式工具 (messaging, clarify) 和递归调度 (cronjob)
3. **跨进程锁**: 防止并发修改 jobs.json

---

## 16. CLI 子系统

### 16.1 核心模块

| 模块 | 功能 | 规模 |
| ---- | ---- | ---- |
| `hermes_cli/main.py` | 主入口、命令分发 | ~12,500 行 |
| `hermes_cli/commands.py` | 斜杠命令注册表 | ~2,000 行 |
| `hermes_cli/gateway.py` | 网关启动管理 | ~7,000 行 |
| `hermes_cli/web_server.py` | 仪表板 WebSocket 服务 | ~12,000 行 |
| `hermes_cli/models.py` | 模型/提供商配置 | ~4,000 行 |
| `hermes_cli/doctor.py` | 诊断工具 | ~2,300 行 |
| `hermes_cli/kanban.py` | 看板多代理编排 | ~2,800 行 |

### 16.2 斜杠命令系统

```python
@dataclass(frozen=True)
class CommandDef:
    name: str                    # "background"
    description: str             # 描述
    category: str                # "Session", "Configuration"
    aliases: tuple[str, ...]     # ("bg",)
    args_hint: str               # "<prompt>"
    cli_only: bool = False       # CLI 专用
    gateway_only: bool = False   # 网关专用
```

**命令分类**:

- **Session**: new, clear, history, save, retry, undo, branch, compress
- **Configuration**: model, config, setup, tools, env
- **Tools**: terminal, browser, read_file, web_search
- **Skills**: skill, skills
- **Gateway**: start, approve, deny
- **Cron**: cron (管理定时任务)

### 16.3 配置文件结构

```text
~/.hermes/
├── config.yaml              # 行为配置 (非 secrets)
├── .env                     # API 密钥 (secrets only)
├── state.db                 # SQLite 会话数据库
├── logs/
│   ├── agent.log            # INFO+ 主日志
│   ├── errors.log           # WARNING+ 错误日志
│   └── gateway.log          # 网关日志
├── skills/                  # 用户技能
├── cron/                    # 定时任务
├── profiles/                # 多配置文件
└── active_profile           # 当前活跃配置名
```

---

## 17. 安全设计

### 17.1 文件写入防护

```python
# 受保护的精确路径 (禁止写入)
~/.ssh/authorized_keys, id_rsa, id_ed25519, config
~/.hermes/.env, .anthropic_oauth.json
~/.netrc, .pgpass, .npmrc, .pypirc, .git-credentials
/etc/sudoers, /etc/passwd, /etc/shadow

# 受保护的前缀 (目录)
~/.ssh/, ~/.aws/, ~/.gnupg/, ~/.kube/
/etc/sudoers.d/, /etc/systemd/
~/.docker/, ~/.azure/, ~/.config/gh/
```

### 17.2 提示注入防护

```python
# 上下文文件扫描 (AGENTS.md, .cursorrules, SOUL.md)
from tools.threat_patterns import scan_for_threats

def _scan_context_content(content: str, filename: str) -> str:
    """扫描上下文文件内容，阻止注入攻击"""
    findings = scan_for_threats(content, scope="context")
    if findings:
        return f"[BLOCKED: {filename} contained potential prompt injection]"
    return content
```

### 17.3 其他安全措施

- **SSL 证书验证** (`agent/ssl_guard.py`)
- **消息清理**: 非 ASCII、surrogate 字符处理
- **敏感信息编辑**: 自动隐藏 API 密钥、令牌、密码
- **URL 安全检查** (`tools/url_safety.py`)
- **路径安全检查** (`tools/path_security.py`)
- **Tirith 安全策略** (`tools/tirith_security.py`)

### 17.4 安全工具集隔离

不同信任等级使用不同的工具集：

| 信任等级 | 工具集 | 说明 |
| ---- | ---- | ---- |
| 完全信任 (CLI) | `_HERMES_CORE_TOOLS` | 所有 40+ 工具 |
| 部分信任 (Gateway) | 用户可配置 | 按需限制 |
| 不信任 (Webhook) | `_HERMES_WEBHOOK_SAFE_TOOLS` | 仅 4 个只读工具 |
| 定时任务 | 禁用 messaging/clarify/cronjob | 防止交互和递归 |

---

## 18. 关键设计模式总结

### 18.1 设计模式

| 模式 | 应用位置 | 说明 |
| ---- | ---- | ---- |
| **注册表模式** | `tools/registry.py`, `agent/transports/`, browser/image_gen/web_search registries | 统一管理可插拔组件 |
| **适配器模式** | `agent/transports/`, `gateway/platforms/` | 统一接口适配不同提供商/平台 |
| **策略模式** | `credential_pool.py` (轮换策略), `context_compressor.py` (压缩引擎) | 运行时切换算法 |
| **工厂模式** | `agent/auxiliary_client.py`, `gateway/platform_registry.py` | 延迟创建实例 |
| **单例模式** | `agent/lsp/` (LSPService), `model_tools.py` (事件循环) | 全局唯一实例 |
| **观察者模式** | `gateway/hooks.py`, 插件钩子 | 事件驱动扩展 |
| **门面模式** | `run_agent.py` (AIAgent 类) | 简化复杂子系统接口 |

### 18.2 核心设计原则

| 原则 | 实现 |
| ---- | ---- |
| **提示缓存优先** | 会话内不修改系统提示、不切换工具集 |
| **核心窄腰** | 新功能优先级: 技能 → CLI命令 → 插件 → MCP → 核心工具 |
| **精确依赖锁定** | `==X.Y.Z` 无范围，防供应链攻击 |
| **配置集中** | 行为设置 → `config.yaml`；密钥 → `.env` |
| **最小权限** | 不同来源使用不同工具集 |
| **优雅降级** | WAL → DELETE 模式，导入失败 → 跳过 |
| **跨平台兼容** | 统一的 Linux/macOS/Windows 路径处理 |

### 18.3 扩展点

| 扩展点 | 位置 | 说明 |
| ---- | ---- | ---- |
| 新工具 | `tools/` + `registry.register()` | 实现新工具能力 |
| 新工具集 | `toolsets.TOOLSETS` | 定义工具分组 |
| 新平台 | `gateway/platforms/` + 实现 BasePlatformAdapter | 接入新消息平台 |
| 新模型提供商 | `agent/transports/` + 实现 ProviderTransport | 接入新 LLM |
| 新记忆后端 | `plugins/memory/` + 实现 MemoryProvider | 接入新记忆系统 |
| 新技能 | `skills/<category>/<name>/SKILL.md` | 注入新知识 |
| 新插件 | `plugins/<name>/` + plugin.yaml + register() | 打包扩展能力 |

---

## 附录：关键文件速查

| 文件 | 规模 | 核心职责 |
| ---- | ---- | ---- |
| `run_agent.py` | ~5,300 行 | AIAgent 类，会话循环主控制器 |
| `model_tools.py` | ~1,230 行 | 工具编排：发现、过滤、缓存、分发 |
| `toolsets.py` | ~850 行 | 工具集定义与递归解析 |
| `hermes_state.py` | ~3,600 行 | SessionDB：SQLite + FTS5 会话存储 |
| `trajectory_compressor.py` | ~1,400 行 | 训练数据轨迹压缩 |
| `agent/conversation_loop.py` | ~3,900 行 | 会话循环实现 |
| `agent/context_compressor.py` | ~600 行 | 上下文自动压缩 |
| `agent/prompt_builder.py` | ~1,600 行 | 系统提示组装 |
| `agent/error_classifier.py` | ~1,300 行 | API 错误分类与故障转移 |
| `agent/credential_pool.py` | ~2,500 行 | 多凭证池管理 |
| `agent/curator.py` | ~2,000 行 | 技能自动维护 |
| `agent/tool_executor.py` | ~400 行 | 工具调用执行引擎 |
| `agent/tool_guardrails.py` | ~400 行 | 工具循环防护 |
| `agent/memory_manager.py` | ~300 行 | 记忆提供商编排 |
| `tools/registry.py` | ~400 行 | 工具注册表 |
| `gateway/run.py` | ~800KB | 网关启动器 |
| `gateway/session.py` | ~1,000 行 | 网关会话管理 |
| `gateway/platforms/base.py` | ~2,800 行 | 平台适配器基类 |
| `hermes_cli/main.py` | ~12,500 行 | CLI 主入口 |
| `hermes_cli/commands.py` | ~2,000 行 | 斜杠命令注册表 |
| `cron/scheduler.py` | ~800 行 | 定时任务调度器 |

---

> **文档生成时间**: 2026-06-16
>
> **分析范围**: hermes-agent-main 全量源码
>
> **技术栈**: Python 3.11+, SQLite WAL + FTS5, OpenAI SDK, asyncio, prompt_toolkit, Rich, FastAPI
