# nanobot 架构设计深度解析

> **版本**: v0.2.1 | **作者**: Xubin Ren & nanobot contributors | **语言**: Python 3.11+ / TypeScript
>
> 本文档基于 nanobot-main 源码深度分析，面向技术人员，涵盖整体架构、框架设计和核心模块的详细设计。

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [Agent 核心模块](#3-agent-核心模块)
4. [LLM 提供商系统](#4-llm-提供商系统)
5. [消息通道系统](#5-消息通道系统)
6. [消息总线系统](#6-消息总线系统)
7. [会话管理系统](#7-会话管理系统)
8. [配置系统](#8-配置系统)
9. [技能系统](#9-技能系统)
10. [定时任务系统](#10-定时任务系统)
11. [命令路由系统](#11-命令路由系统)
12. [安全设计](#12-安全设计)
13. [WebUI 系统](#13-webui-系统)
14. [关键设计模式总结](#14-关键设计模式总结)

---

## 1. 项目概述

nanobot 是一个**轻量级开源 AI 智能体框架**，由 Xubin Ren 主导开发。它围绕一个小型 Agent 循环构建，接收来自聊天通道的消息，调用 LLM 提供商，执行工具，并管理会话记忆。配备 React/TypeScript WebUI 和 20+ 消息平台支持。

### 1.1 设计哲学

| 原则 | 说明 |
| ---- | ---- |
| **核心保持小，边缘扩展** | `agent/loop.py` 和 `agent/runner.py` 是关键路径，新功能通过 channels/tools/skills/MCP 添加 |
| **更少结构，更多智能** | 优先简洁可读代码，仅在移除真实复杂性时添加结构 |
| **优先重复而非过度抽象** | 通道和提供商允许重复相似逻辑，不为消除重复引入复杂基类 |
| **最小化改动解决真实问题** | 仅改变必要部分，不在功能 PR 中捆绑无关重构 |

### 1.2 项目规模

| 指标 | 数量 |
| ---- | ---- |
| Python 模块 | ~80 |
| 消息平台适配器 | 20+ |
| LLM 提供商 | 50+ |
| 内置技能 | 12 |
| 内置工具 | 23+ |

---

## 2. 整体架构

### 2.1 异步消息驱动架构

nanobot 采用**异步消息驱动架构**，通过 `MessageBus` 解耦三个核心子系统：

```text
外部平台 → Channels → MessageBus → AgentLoop → Providers → LLM API
                          ↓
                    OutboundMessage
                          ↓
                      Channels → 外部平台
```

### 2.2 分层架构图

```text
┌─────────────────────────────────────────────────────────────────┐
│                         接入层                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ CLI (typer)   │  │ WebUI (React) │  │ API (/v1/chat)       │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                                │
┌────────────────────────────────┼────────────────────────────────┐
│                         通道层 (Channels)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │ Telegram │ │ Discord  │ │  Slack   │ │ WeChat/QQ/Feishu │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘   │
│                         │ MessageBus                             │
└─────────────────────────┼────────────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│                         核心层 (Agent Core)                       │
│  ┌──────────────────────┼────────────────────────────────────┐   │
│  │                   AgentLoop                                │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │   │
│  │  │ ContextBuilder│  │ AgentRunner  │  │ Consolidator   │  │   │
│  │  │ (上下文构建)   │  │ (LLM 循环)   │  │ (记忆压缩)     │  │   │
│  │  └──────────────┘  └──────────────┘  └────────────────┘  │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │   │
│  │  │ ToolRegistry  │  │ MemoryStore  │  │ SessionManager │  │   │
│  │  │ (工具注册表)   │  │ (文件存储)   │  │ (会话管理)     │  │   │
│  │  └──────────────┘  └──────────────┘  └────────────────┘  │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│                         提供商层 (Providers)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │ OpenAI   │ │Anthropic │ │  Azure   │ │ Bedrock/DeepSeek │   │
│  │ Compat   │ │ Claude   │ │  OpenAI  │ │ /Gemini/...      │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 目录结构

```text
nanobot-main/
├── nanobot/                       # Python 核心包
│   ├── agent/                     # Agent 核心
│   │   ├── loop.py                # AgentLoop — 主循环 (1800+ 行)
│   │   ├── runner.py              # AgentRunner — LLM 对话循环
│   │   ├── memory.py              # MemoryStore + Consolidator
│   │   ├── context.py             # ContextBuilder — 上下文组装
│   │   ├── hook.py                # 生命周期钩子
│   │   ├── skills.py              # 技能加载器
│   │   ├── subagent.py            # 子代理管理
│   │   ├── autocompact.py         # 自动压缩
│   │   └── tools/                 # 工具子系统 (23+ 工具)
│   │       ├── base.py            # Tool 抽象基类
│   │       ├── registry.py        # ToolRegistry 注册表
│   │       ├── loader.py          # ToolLoader 动态发现
│   │       ├── filesystem.py      # 文件操作
│   │       ├── shell.py           # Shell 执行
│   │       ├── web.py             # Web 搜索/获取
│   │       ├── mcp.py             # MCP 协议
│   │       └── ...
│   ├── providers/                 # LLM 提供商 (50+)
│   │   ├── base.py                # LLMProvider 抽象基类
│   │   ├── factory.py             # 提供商工厂
│   │   ├── registry.py            # ProviderSpec 注册表
│   │   ├── openai_compat_provider.py
│   │   ├── anthropic_provider.py
│   │   └── ...
│   ├── channels/                  # 消息通道 (20+)
│   │   ├── base.py                # BaseChannel 抽象基类
│   │   ├── manager.py             # ChannelManager
│   │   ├── telegram.py, discord.py, slack.py, ...
│   │   └── ...
│   ├── bus/                       # 消息总线
│   │   ├── queue.py               # MessageBus
│   │   ├── events.py              # InboundMessage / OutboundMessage
│   │   └── runtime_events.py      # 运行时事件
│   ├── session/                   # 会话管理
│   │   ├── manager.py             # SessionManager
│   │   ├── keys.py                # 会话键策略
│   │   └── goal_state.py          # 持续目标状态
│   ├── config/                    # 配置系统
│   │   ├── schema.py              # Pydantic 配置模型
│   │   ├── loader.py              # 配置加载/迁移
│   │   └── paths.py               # 运行时路径
│   ├── cron/                      # 定时任务
│   │   ├── service.py             # CronService
│   │   └── types.py               # 数据类型
│   ├── command/                   # 命令路由
│   │   ├── router.py              # CommandRouter
│   │   └── builtin.py             # 内置命令
│   ├── skills/                    # 内置技能 (12 个)
│   ├── security/                  # 安全
│   │   ├── network.py             # SSRF 保护
│   │   └── workspace_access.py    # 工作区访问控制
│   └── web/                       # Web 静态文件 (WebUI dist)
├── webui/                         # React/TypeScript WebUI
│   └── src/
│       ├── components/            # React 组件
│       ├── hooks/                 # 自定义 hooks
│       └── lib/                   # 工具库
├── bridge/                        # TypeScript 桥接服务
├── tests/                         # 测试套件
└── docs/                          # 文档
```

### 2.4 核心数据流

```text
InboundMessage (bus)
    ↓
AgentLoop.run() 消费消息
    ↓
_dispatch(msg) — 获取 session lock + 并发门
    ↓
_process_message(msg)
    ├─ 恢复会话状态
    ├─ 自动压缩检查
    ├─ 命令分发
    ├─ 构建上下文 (ContextBuilder)
    │   ├─ 系统提示词
    │   ├─ 历史记录
    │   └─ 工具定义
    ├─ 调用 AgentRunner.run()
    │   ├─ LLM 调用 (with streaming)
    │   ├─ 工具调用解析
    │   ├─ 工具执行 (ToolRegistry.execute)
    │   ├─ 注入处理 (mid-turn)
    │   └─ 错误恢复
    ├─ 保存会话状态
    ├─ 发送响应 (OutboundMessage)
    └─ 发布运行时事件
```

---

## 3. Agent 核心模块

### 3.1 AgentLoop — 主处理引擎

`AgentLoop` 是 Agent 的主控制器，协调整个对话流程。

```python
class AgentLoop:
    """Agent 的主处理引擎"""

    bus: MessageBus                 # 消息总线
    provider: LLMProvider           # LLM 提供商
    workspace: Path                 # 工作目录
    model: str                      # 当前模型
    max_iterations: int             # 最大迭代次数
    context: ContextBuilder         # 上下文构建器
    sessions: SessionManager        # 会话管理
    tools: ToolRegistry             # 工具注册表
    runner: AgentRunner             # LLM 执行器
    consolidator: Consolidator      # 记忆压缩

    _active_tasks: dict             # 活跃任务 (per session)
    _pending_queues: dict           # 待处理消息队列 (mid-turn injection)
    _session_locks: dict            # 会话锁 (串行处理)
    _concurrency_gate: Semaphore    # 并发控制
```

**状态机** — 8 状态驱动：

```text
TurnState: RESTORE → COMPACT → COMMAND → BUILD → RUN → SAVE → RESPOND → DONE
```

**并发控制**：

- **Per-session 串行**: `_session_locks[session_key]` 确保同一会话消息顺序处理
- **Cross-session 并发**: 不同会话可并行处理
- **全局并发门**: `NANOBOT_MAX_CONCURRENT_REQUESTS` (默认 3)
- **Mid-turn 注入**: 活跃会话的新消息进入 `_pending_queues`，不创建新任务

### 3.2 AgentRunner — LLM 对话循环

`AgentRunner` 是 LLM 对话循环的核心执行引擎，不涉及产品层逻辑。

```python
@dataclass
class AgentRunSpec:
    """单次 Agent 执行的配置"""
    initial_messages: list[dict]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    temperature: float | None = None
    hook: AgentHook | None = None
    concurrent_tools: bool = False
    workspace: Path | None = None
    session_key: str | None = None
    context_window_tokens: int | None = None
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    llm_timeout_s: float | None = None
    goal_active_predicate: Callable | None = None

@dataclass
class AgentRunResult:
    """执行结果"""
    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str]
    usage: dict[str, int]
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]]
    had_injections: bool = False
```

**核心执行流程**：

```text
1. before_run() hook
2. _run_core() 核心循环:
   - 调用 LLM (with streaming)
   - 解析工具调用
   - 执行工具 (concurrent_tools 支持)
   - 处理工具结果
   - 处理注入 (_try_drain_injections)
   - 错误恢复 (length recovery, empty retry)
3. after_run() / on_error() hook
```

**错误恢复机制**：

| 恢复类型 | 最大次数 | 说明 |
| ---- | ---- |
| Length Recovery | 3 | 消息过长时重试 |
| Empty Retry | 2 | LLM 返回空响应时重试 |
| Injection Cycles | 5 | 支持最多 5 个注入周期 |
| Goal Continuation | 无限 | 持续目标的自动继续 |

### 3.3 记忆系统

#### MemoryStore — 文件 I/O 层

纯文件 I/O 层，管理持久化记忆文件：

```text
workspace/
├── SOUL.md                  # 代理灵魂/个性
├── USER.md                  # 用户信息
└── memory/
    ├── MEMORY.md            # 长期记忆 (事实)
    ├── history.jsonl        # 历史记录 (append-only)
    ├── .cursor              # 历史游标
    └── .dream_cursor        # Dream 处理游标
```

关键特性：

- 线程安全的 cursor 分配 (`_append_lock`)
- 自动迁移遗留 HISTORY.md 到 history.jsonl
- 条目大小限制 (64KB hard cap)
- 支持 session_key 过滤
- Git 版本控制 (通过 GitStore)

#### Consolidator — 记忆压缩

轻量级消息压缩，将驱逐的消息总结到历史记录：

- **压缩策略**: 按 user-turn 边界选择
- **Token 预算感知**: consolidation_ratio (默认 0.5)
- **支持 replay 窗口溢出处理**

### 3.4 ContextBuilder — 上下文组装

组装 Agent 系统提示词和消息历史：

```python
class ContextBuilder:
    def build_system_prompt():
        # 1. 身份信息 (identity.md)
        # 2. Bootstrap 文件 (AGENTS.md, SOUL.md, USER.md)
        # 3. 工具契约 (tool_contract.md)
        # 4. 长期记忆
        # 5. 活跃技能
        # 6. 技能摘要
        # 7. 最近历史 (50 条，32KB 上限)
        # 8. 会话摘要
```

### 3.5 工具系统

#### ToolRegistry — 工具注册表

```python
class ToolRegistry:
    def register(tool: Tool)              # 注册工具
    def get(name: str) -> Tool | None     # 获取工具
    def get_definitions() -> list[dict]   # 获取工具定义 (缓存)
    def prepare_call(name, params)        # 解析、强制转换、验证
    async def execute(name, params)       # 执行工具
```

特性：

- 工具名称规范化建议 (`_suggest_name`)
- 参数强制转换和验证
- 内置工具优先排序，MCP 工具后排序
- 定义缓存 (register/unregister 时失效)

#### Tool — 工具基类

```python
class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str

    @property
    @abstractmethod
    def description(self) -> str

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]

    async def execute(**kwargs) -> Any    # 执行工具
    def to_schema() -> dict               # OpenAI 工具定义
    def cast_params(params) -> dict       # 参数强制转换
    def validate_params(params) -> list   # 参数验证
```

#### 内置工具清单 (23+)

| 工具 | 功能 |
| ---- | ---- |
| `read_file` / `write_file` / `edit_file` | 文件操作 |
| `list_dir` / `find_files` / `grep` | 目录与搜索 |
| `exec` / `shell` | 命令执行 |
| `web_search` / `web_fetch` | Web 搜索与抓取 |
| `apply_patch` | 代码补丁应用 |
| `spawn` | 子任务生成 |
| `cron` | 定时任务管理 |
| `image_generation` | 图像生成 |
| `mcp_*` | MCP 协议工具 |
| `long_task_*` | 长任务管理 |
| `message` | 消息发送 |

#### ToolLoader — 工具动态发现

```text
发现流程:
1. 扫描 nanobot.agent.tools 包中的模块
2. 跳过 _SKIP_MODULES (base, schema, registry 等)
3. 查找 Tool 的非抽象子类
4. 检查 enabled() 和 _scopes 过滤
5. 调用 create(ctx) 创建实例
6. 注册到 ToolRegistry

支持:
- pkgutil 自动扫描
- entry_points 插件
- _plugin_discoverable 标志控制
```

---

## 4. LLM 提供商系统

### 4.1 基类接口

```python
class LLMProvider(ABC):
    """LLM 提供商基类"""

    # 核心方法
    async def chat(messages, tools, model, ...) -> LLMResponse
    async def chat_stream(messages, ..., on_content_delta, ...) -> LLMResponse
    async def chat_stream_with_retry(...) -> LLMResponse
    async def chat_with_retry(...) -> LLMResponse

    # 错误处理
    _TRANSIENT_ERROR_MARKERS = (...)       # 429, 500, 502, 503, 504, timeout
    _RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
    _NON_RETRYABLE_429_ERROR_TOKENS = {...}  # 配额、计费
    _RETRYABLE_429_ERROR_TOKENS = {...}      # 速率限制
```

### 4.2 响应数据结构

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str               # "stop", "tool_calls", "error"
    usage: dict[str, int]
    reasoning_content: str | None    # Kimi, DeepSeek-R1
    thinking_blocks: list[dict] | None  # Anthropic extended thinking
    error_status_code: int | None
    error_kind: str | None           # "timeout", "connection"
    error_type: str | None           # "insufficient_quota"
    error_code: str | None           # "rate_limit_exceeded"
    error_should_retry: bool | None
```

### 4.3 提供商注册表 (SSOT 设计)

采用**单一真实来源**设计，50+ 提供商元数据集中管理：

```python
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(name="openai", keywords=("gpt-4", "gpt-3.5")),
    ProviderSpec(name="anthropic", keywords=("claude",)),
    ProviderSpec(name="deepseek", keywords=("deepseek",)),
    ProviderSpec(name="openrouter", is_gateway=True, detect_by_key_prefix="sk-or-"),
    # ... 50+ 提供商
)

@dataclass(frozen=True)
class ProviderSpec:
    name: str                        # 配置字段名
    keywords: tuple[str, ...]        # 模型名匹配
    env_key: str                     # 环境变量
    display_name: str
    backend: str                     # "openai_compat" | "anthropic" | "azure_openai" | "bedrock"

    # 网关检测
    is_gateway: bool
    detect_by_key_prefix: str        # API key 前缀匹配
    detect_by_base_keyword: str      # api_base URL 匹配
    default_api_base: str

    # 思维链支持
    thinking_style: str              # "thinking_type" | "enable_thinking" | "reasoning_split"
    gateway_reasoning_style: str     # "reasoning_effort"

    # 特殊标记
    is_oauth: bool                   # GitHub Copilot
    is_direct: bool                  # 用户提供完整配置
    supports_prompt_caching: bool    # Anthropic
```

### 4.4 工厂模式

```python
def make_provider(config, preset_name=None, preset=None, model=None) -> LLMProvider:
    """创建提供商 (带故障转移)"""

    # 1. 解析模型预设
    # 2. 查找提供商规范 (自动检测)
    # 3. 根据 backend 实例化 (openai_compat/anthropic/azure/bedrock)
    # 4. 应用生成设置
    # 5. 包装故障转移链 (如有 fallback_models)

    return FallbackProvider(primary=provider, fallback_presets=...) if fallback else provider
```

### 4.5 核心机制

- **多后端支持**: OpenAI 兼容、Anthropic、Azure、Bedrock、GitHub Copilot、OpenAI Codex
- **网关自动检测**: 通过 API key 前缀或 api_base URL 自动识别
- **故障转移链**: 支持多个备用模型自动切换
- **智能重试**: 区分瞬时错误和永久错误，指数退避 (1s, 2s, 4s)
- **流式支持**: `chat_stream()` 用于实时响应
- **思维链支持**: Kimi、DeepSeek-R1、Anthropic extended thinking

---

## 5. 消息通道系统

### 5.1 基类接口

```python
class BaseChannel(ABC):
    """聊天通道抽象基类"""

    name: str = "base"
    display_name: str = "Base"
    send_progress: bool = True       # 发送进度更新
    send_tool_hints: bool = False    # 发送工具提示
    show_reasoning: bool = True      # 显示推理过程

    def __init__(self, config: Any, bus: MessageBus):
        self.config = config
        self.bus = bus

    @abstractmethod
    async def start(self) -> None:    # 启动监听
    @abstractmethod
    async def stop(self) -> None:     # 停止清理
    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:  # 发送消息

    # 流式支持 (可选覆盖)
    async def send_delta(self, chat_id, delta, metadata=None)
    async def send_reasoning_delta(self, chat_id, delta, metadata=None)
    async def send_reasoning_end(self, chat_id, metadata=None)
```

### 5.2 权限管理

三层权限体系：

```text
权限检查: * (通配符) > allowlist > pairing store > deny

is_allowed(sender_id):
    1. "*" 在 allow_from → 允许所有
    2. sender_id 在 allow_from → 显式允许
    3. sender_id 在配对码存储中 → 已批准
    4. 默认 → 拒绝 (DM 中发送配对码)
```

### 5.3 ChannelManager — 通道管理器

```python
class ChannelManager:
    """管理聊天通道和协调消息路由"""

    def _init_channels():
        # 1. pkgutil 扫描 + entry_points 发现
        # 2. 仅导入启用的通道 (延迟加载)
        # 3. 实例化并配置通道属性

    async def _dispatch_outbound():
        """分发出站消息"""
        # 1. 推理内容路由 (仅 show_reasoning 通道)
        # 2. 进度消息过滤
        # 3. 流式消息合并 (减少 API 调用)
        # 4. 重复消息抑制 (内容指纹)
        # 5. 带重试的发送 (指数退避)
```

### 5.4 支持的平台 (20+)

Telegram, Discord, Slack, WhatsApp, WeChat (微信), QQ, Feishu (飞书), WeCom (企业微信), DingTalk (钉钉), Matrix, MS Teams, Email, SMS, WebSocket, MoChat, API Server

---

## 6. 消息总线系统

### 6.1 MessageBus

极简设计，基于 `asyncio.Queue` 的双向消息队列：

```python
class MessageBus:
    """异步消息总线，解耦聊天通道和代理核心"""

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg)    # 通道 → 代理
    async def consume_inbound(self) -> msg  # 代理消费
    async def publish_outbound(self, msg)   # 代理 → 通道
    async def consume_outbound(self) -> msg # 通道消费
```

### 6.2 事件数据结构

```python
@dataclass
class InboundMessage:
    channel: str                     # "telegram", "discord", ...
    sender_id: str                   # 用户标识符
    chat_id: str                     # 聊天标识符
    content: str                     # 消息文本
    media: list[str]                 # 媒体 URL
    metadata: dict[str, Any]         # 通道特定数据
    session_key_override: str | None # 线程作用域会话

@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    metadata: dict[str, Any]         # _stream_delta, _reasoning_delta, ...
```

---

## 7. 会话管理系统

### 7.1 存储格式

使用 JSONL (JSON Lines) 格式存储会话：

```text
~/.nanobot/workspace/sessions/{safe_key}.jsonl

第一行: {"_type": "metadata", "created_at": "...", ...}
后续行: {"role": "user", "content": "..."}
        {"role": "assistant", "content": "..."}
```

### 7.2 Session 数据类

```python
@dataclass
class Session:
    key: str                          # channel:chat_id
    messages: list[dict]              # 对话消息列表
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
    last_consolidated: int            # 已压缩消息数

    def get_history(max_messages=120, max_tokens=None):
        """按消息数和 token 预算切片历史"""
```

### 7.3 SessionManager

```python
class SessionManager:
    def get_or_create(key: str) -> Session
    def save(session: Session, *, fsync: bool = False)
    def _repair(key: str) -> Session | None  # 损坏文件恢复
    def delete_session(key: str) -> bool
    def fork_session_before_user_index(...)   # 会话分支
```

特性：
- **原子写入**: 临时文件 + `os.replace()` 确保一致性
- **损坏恢复**: `_repair()` 方法自动修复
- **会话分支**: 支持从指定消息位置 fork

### 7.4 会话键策略

```python
UNIFIED_SESSION_KEY = "unified:default"   # 跨渠道统一会话
session_key = f"{channel}:{chat_id}"      # 渠道特定会话
```

---

## 8. 配置系统

### 8.1 Pydantic 配置模型

```python
class Config(BaseSettings):
    agents: AgentsConfig              # 代理默认值
    channels: ChannelsConfig          # 渠道配置
    transcription: TranscriptionConfig # 音频转录
    providers: ProvidersConfig        # LLM 提供商
    api: ApiConfig                    # API 网关
    gateway: GatewayConfig            # 网关设置
    tools: ToolsConfig                # 工具配置
    model_presets: dict[str, ModelPresetConfig]
```

### 8.2 AgentDefaults

```python
class AgentDefaults(Base):
    workspace: str = "~/.nanobot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    max_tokens: int = 8192
    context_window_tokens: int = 65_536
    temperature: float = 0.1
    fallback_models: list[FallbackCandidate]
    max_tool_iterations: int = 200
    max_concurrent_subagents: int = 1
    timezone: str = "UTC"
    bot_name: str = "nanobot"
    unified_session: bool = False
    session_ttl_minutes: int = 0
    consolidation_ratio: float = 0.5
    dream: DreamConfig
```

### 8.3 配置加载流程

```text
load_config(config_path):
    1. 解析工具配置引用
    2. 从 JSON 加载
    3. 迁移旧格式 (_migrate_config)
    4. Pydantic 验证 (Config.model_validate)
    5. 应用 SSRF 白名单
    6. 解析环境变量引用 (${VAR})
```

### 8.4 运行时路径

```text
~/.nanobot/
├── config.json                    # 主配置
├── workspace/
│   └── sessions/                  # 会话 JSONL 文件
├── cron/                          # 定时任务存储
├── logs/                          # 日志目录
└── webui/                         # WebUI 持久化数据
```

---

## 9. 技能系统

### 9.1 技能格式

```yaml
---
name: github
description: "Interact with GitHub using the `gh` CLI"
metadata:
  nanobot:
    emoji: "🐙"
    requires:
      bins: ["gh"]
    install: [...]
---

# Markdown instructions for the agent
```

### 9.2 内置技能

| 技能 | 功能 |
| ---- | ---- |
| `github` | GitHub CLI 集成 |
| `weather` | 天气信息查询 |
| `summarize` | URL/文件/视频摘要 |
| `tmux` | 远程 tmux 会话控制 |
| `clawhub` | 技能注册表搜索安装 |
| `skill-creator` | 新技能创建工具 |
| `long-goal` | 长期目标管理 |
| `memory` | 内存管理 |
| `cron` | 定时任务管理 |
| `image-generation` | 图像生成 |
| `my` | 自省工具 |
| `update-setup` | 更新和设置 |

---

## 10. 定时任务系统

### 10.1 调度类型

```python
@dataclass
class CronSchedule:
    kind: Literal["at", "every", "cron"]
    at_ms: int | None              # 单次执行时间戳
    every_ms: int | None           # 间隔 (毫秒)
    expr: str | None               # Cron 表达式
    tz: str | None                 # 时区 (仅 cron)
```

### 10.2 任务结构

```python
@dataclass
class CronJob:
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule
    payload: CronPayload            # 执行内容
    state: CronJobState             # 运行状态
    delete_after_run: bool

@dataclass
class CronPayload:
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str                    # 代理消息
    session_key: str | None         # 目标会话
    origin_channel: str | None      # 源渠道
    origin_chat_id: str | None      # 源聊天 ID
```

### 10.3 CronService

```python
class CronService:
    def __init__(self, store_path: Path, on_job: Callable, max_sleep_ms: int)
    def add_job(job: CronJob)
    def remove_job(job_id: str)
    async def run()                 # 主事件循环

# 调度计算:
# "at"    → 单次执行，检查时间戳是否已过
# "every" → 固定间隔，下次运行 = 现在 + 间隔
# "cron"  → croniter 库计算下次运行时间
```

### 10.4 Dream 记忆巩固

配置化的周期性记忆巩固任务：

```python
class DreamConfig:
    enabled: bool = True             # 启动时注册 Dream 任务
    interval_h: int = 2              # 每 2 小时
    model_override: str | None       # 覆盖模型
    max_batch_size: int = 20
    max_iterations: int = 15
```

---

## 11. 命令路由系统

### 11.1 CommandRouter

三层命令分发策略：

```python
class CommandRouter:
    """三层命令分发"""

    def priority(cmd, handler)      # 优先级命令 (/stop) — 无需锁
    def exact(cmd, handler)         # 精确匹配 (/new)
    def prefix(pfx, handler)        # 前缀匹配 (/team ) — 最长前缀优先

    async def dispatch_priority(ctx)
    async def dispatch(ctx)
```

### 11.2 内置命令

```python
BUILTIN_COMMANDS = (
    "/new",           # 新对话
    "/stop",          # 停止当前任务
    "/restart",       # 重启 nanobot
    "/status",        # 显示状态
    "/model",         # 切换模型预设
    "/history",       # 显示对话历史
    "/goal",          # 启动长期目标
    "/dream",         # 运行 Dream
    "/dream-log",     # 显示 Dream 日志
    "/dream-restore", # 恢复记忆
    # ...
)
```

---

## 12. 安全设计

### 12.1 SSRF 保护

阻止对内网的网络请求：

```python
_BLOCKED_NETWORKS = [
    "0.0.0.0/8",          # 此主机
    "10.0.0.0/8",         # RFC1918
    "127.0.0.0/8",        # 环回
    "169.254.0.0/16",     # 链路本地/云元数据
    "172.16.0.0/12",      # RFC1918
    "192.168.0.0/16",     # RFC1918
    "::1/128",            # IPv6 环回
    "fc00::/7",           # IPv6 唯一本地
    "fe80::/10",          # IPv6 链路本地
]

def validate_url_target(url, *, allow_loopback=False)
def configure_ssrf_whitelist(cidrs)  # 如 Tailscale
```

### 12.2 工作区访问控制

```python
@dataclass(frozen=True)
class WorkspaceScope:
    project_path: Path
    access_mode: WorkspaceAccessMode  # "restricted" | "full"
    restrict_to_workspace: bool
    sandbox_status: WorkspaceSandboxStatus
    source_channel: str | None

class WorkspaceScopeResolver:
    def for_message(msg, session_metadata) -> WorkspaceScope
    def for_turn(*, channel, message_metadata, session_metadata)
```

### 12.3 沙箱检测

- **系统沙箱**: macOS App Sandbox, Bubblewrap
- **应用级守卫**: nanobot 路径检查
- **禁用**: 无限制访问

---

## 13. WebUI 系统

### 13.1 技术栈

| 技术 | 版本 |
| ---- | ---- |
| React | 18.3.1 |
| TypeScript | 5.7.2 |
| Vite | 5.4.11 |
| Tailwind CSS | 3.4.17 |
| Radix UI | 最新 |
| react-markdown | 9.0.1 |
| KaTeX | 0.16.21 |
| i18next | 26.0.6 |
| Vitest | 2.1.8 |

### 13.2 构建集成

```text
cd webui && bun run build
    → 输出: nanobot/web/dist/
    → hatch 打包进 Python wheel
    → 网关直接 serve 静态文件
```

---

## 14. 关键设计模式总结

### 14.1 设计模式

| 模式 | 应用位置 | 说明 |
| ---- | ---- |
| **工厂模式** | `providers/factory.py`, `tools/loader.py` | 动态创建 LLM 提供商和工具实例 |
| **策略模式** | `providers/base.py` (LLMProvider), `command/router.py` | 不同提供商不同实现，不同命令不同分发 |
| **观察者模式** | `bus/queue.py` (MessageBus) | 通道和代理通过消息队列解耦 |
| **模板方法** | `channels/base.py` (BaseChannel) | 定义通道骨架，子类实现具体步骤 |
| **装饰器模式** | `providers/fallback_provider.py` | 为提供商动态添加故障转移 |
| **注册表模式** | `providers/registry.py`, `tools/registry.py` | 集中管理元数据，SSOT 设计 |
| **状态机模式** | `agent/loop.py` (TurnState) | 8 状态驱动会话处理 |
| **钩子模式** | `agent/hook.py` (AgentHook, CompositeHook) | 生命周期扩展点 |

### 14.2 核心设计原则

| 原则 | 实现 |
| ---- | ---- |
| **核心窄腰** | agent/loop.py 和 runner.py 是唯二关键路径 |
| **异步优先** | asyncio 贯穿全栈 |
| **显式优于魔法** | 配置在 schema.py 显式声明，错误清晰抛出 |
| **延迟加载** | 仅导入启用的通道，降低启动时间 |
| **原子操作** | 临时文件 + os.replace() 确保数据一致性 |
| **最小权限** | 三层权限 + 工作区隔离 + SSRF 保护 |

### 14.3 扩展点

| 扩展点 | 位置 | 说明 |
| ---- | ---- |
| 新 LLM 提供商 | `providers/registry.py` + `providers/xxx_provider.py` | 添加 ProviderSpec + 实现 LLMProvider |
| 新消息通道 | `channels/xxx.py` + 继承 BaseChannel | 实现 start/stop/send |
| 新工具 | `agent/tools/xxx.py` + 继承 Tool | 自动被 ToolLoader 发现 |
| 新技能 | `skills/<name>/SKILL.md` | YAML 元数据 + Markdown 指令 |
| 新命令 | `command/builtin.py` | 注册到 CommandRouter |
| 生命周期钩子 | `agent/hook.py` | 实现 AgentHook 接口 |

### 14.4 与 Hermes Agent 对比

| 维度 | nanobot | Hermes Agent |
| ---- | ---- | ---- | ---- | ---- |
| 定位 | 轻量级个人助手 | 全功能自进化 Agent |
| 代码规模 | ~80 模块 | 200+ 模块 |
| 记忆存储 | 文件 I/O (JSONL + Markdown) | SQLite WAL + FTS5 |
| 提供商管理 | SSOT 注册表 + 自动检测 | Transport 层 + 多凭证池 |
| 会话管理 | JSONL 文件 + 原子写入 | SQLite + 链式结构 |
| 插件系统 | entry_points + pkgutil 扫描 | 四层发现 + PluginContext API |
| 上下文压缩 | Consolidator (轻量) | ContextCompressor (全功能) |
| 安全 | SSRF + 工作区隔离 | 文件写入防护 + 提示注入扫描 + 凭证管理 |

---

## 附录：关键文件速查

| 文件 | 规模 | 核心职责 |
| ---- | ---- | ---- | ---- | ---- |
| `agent/loop.py` | ~1,800 行 | AgentLoop — 主循环、消息分发、状态管理 |
| `agent/runner.py` | ~1,200 行 | AgentRunner — LLM 执行循环、工具调用、错误恢复 |
| `agent/memory.py` | ~900 行 | MemoryStore + Consolidator — 记忆存储与压缩 |
| `agent/context.py` | ~300 行 | ContextBuilder — 系统提示词组装 |
| `agent/tools/registry.py` | ~180 行 | ToolRegistry — 工具注册、参数验证、执行 |
| `agent/tools/base.py` | ~300 行 | Tool ABC — 工具抽象基类 |
| `providers/base.py` | ~700 行 | LLMProvider ABC — 提供商基类与重试 |
| `providers/registry.py` | ~534 行 | 50+ 提供商 SSOT 注册表 |
| `providers/factory.py` | ~178 行 | make_provider() — 提供商工厂 |
| `channels/base.py` | ~256 行 | BaseChannel ABC — 通道基类 |
| `channels/manager.py` | ~389 行 | ChannelManager — 通道管理 |
| `bus/queue.py` | ~44 行 | MessageBus — 异步消息队列 |
| `session/manager.py` | ~700 行 | SessionManager — 会话持久化 |
| `config/schema.py` | ~450 行 | Pydantic 配置模型 |
| `cron/service.py` | ~800 行 | CronService — 定时任务调度 |
| `command/router.py` | ~90 行 | CommandRouter — 命令分发 |
| `security/network.py` | ~160 行 | SSRF 保护 |
| `security/workspace_access.py` | ~430 行 | 工作区访问控制 |

---

> **文档生成时间**: 2026-06-16
>
> **分析范围**: nanobot-main 全量源码
>
> **技术栈**: Python 3.11+, asyncio, Pydantic, Typer, React/TypeScript, Vite, Tailwind CSS
