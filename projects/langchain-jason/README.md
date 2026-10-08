# LangChain-Jason

一个用于课程学习的只读 macOS 目录分析与研究 Agent。项目同时演示 LCEL 固定流程、Agent 自主工具调用与混合路由。

这是个人课程实践项目，主要面向 macOS；[架构设计文档](docs/architecture.md)记录了各模块的实现与设计。联网搜索和 ReAct 模式所需的密钥由使用者自行配置。

## 核心功能

| 工具 | 功能 | 是否需联网 |
| :--- | :--- | :--- |
| `count_directory_files` | 统计文件数、目录数和总容量 | 否 |
| `analyze_file_distribution` | 按扩展名统计数量和容量 | 否 |
| `find_duplicate_files` | 大小预筛 + SHA-256 检测重复文件 | 否 |
| `analyze_file_activity` | 分析近期、长期未修改和大文件 | 否 |
| `web_search` | SerpAPI → Tavily → DuckDuckGo 多源降级 | 是 |
| `inspect_mac_system_health` | 检查 CPU、内存、磁盘、电池和网络接口摘要 | 否 |

## 三种执行模式

| 模式 | 适合场景 | 模型密钥 |
| :--- | :--- | :--- |
| `lcel` | 输入和步骤固定、要求可预测的任务 | 不需要 |
| `react` | 开放式、多步、需要自主选工具的任务 | 需要 `DASHSCOPE_API_KEY` |
| `hybrid` | 先按可解释规则路由，必要时才使用 Agent | 只有路由到 `react` 时需要 |

> [!NOTE]
> 项目中的 `react` 使用 LangChain 1.x `create_agent` 的工具调用循环。它体现“推理—调用工具—观察结果—继续处理”模式，不依赖旧版 `initialize_agent` API。

## 安装

需要 Python 3.10 或更高版本，建议使用 Python 3.11。

```bash
cd projects/langchain-jason
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果要使用 ReAct 或带密钥的搜索源，按 `.env.example` 设置环境变量。程序不会自动读取 `.env`，需要在 shell 或 IDE 运行配置中注入。

## 运行示例

启动持续对话（需要已设置 `DASHSCOPE_API_KEY`；默认使用 ReAct）：

```bash
python app.py --interactive --directory "$PWD"
```

启动后可连续输入问题，例如先询问文件数量，再追问文件类型分布；也可用 `--prompt "先统计文件数量"` 直接发出首轮问题。输入 `退出`、`exit`、`quit` 或按 Ctrl+D 结束。`--interactive` 仅支持 `react` 模式，不能与固定 `--task`、`--query` 同用；如需逐轮 JSON 结果可加 `--json`。

下面的命令仍是单次执行，每次启动后只处理一个请求。

完整目录审计：

```bash
python app.py --mode lcel --task audit \
  --directory "$PWD"
```

单独查找重复文件：

```bash
python app.py --mode hybrid --task duplicates \
  --directory "$PWD" --json
```

检查系统健康：

```bash
python app.py --mode lcel --task system
```

多源搜索：

```bash
python app.py --mode lcel --task search --query "LangChain 1.x create_agent"
```

Agent 自主处理：

```bash
export DASHSCOPE_API_KEY="<your-key>"
python app.py --mode react \
  --directory "$PWD" \
  --prompt "分析这个目录的结构、重复文件和活跃度，给出整理建议"
```

## 配置

- 提示词集中在 `config/prompts.yaml`。
- 运行参数集中在 `config/settings.yaml`。
- 密钥仅通过环境变量注入，不写入 YAML。
- 配置优先级：命令行/调用参数 > 环境变量 > YAML。
- 默认仅允许扫描 `~/Desktop` 下的目录；分析其他路径前必须显式增加 `directory_scan.allowed_roots`。

## 搜索降级规则

`sequential` 策略会按配置顺序调用搜索源。缺少密钥的搜索源会标记为 `unavailable` 并跳过；超时、网络错误、5xx 或结果太少时降级到下一源；401/403 会标记为认证错误并停止本次链路，避免掩盖配置问题。

DuckDuckGo 实现使用 Instant Answer API，它是免密钥的最后降级源，但不等同于完整网页搜索索引。

## 短期记忆

`--interactive` 在同一进程中复用 ReAct Agent 和 `InMemorySessionStore`，按 `--session-id`（默认 `default`）保存最近 20 条用户/助手消息。首轮的 `--directory` 会加入对话输入，后续轮次通过历史消息理解“这个目录”等指代。退出或重启后记忆清空；独立执行的多条 `python app.py` 命令之间不会共享记忆。当前不支持跨进程持久化。

## 目录结构

| 目录/入口 | 职责 |
| :--- | :--- |
| `app.py` | 解析命令参数，装配配置、追踪与执行模式 |
| `cli/` | 持续对话循环、单轮执行追踪及报告输出 |
| `modes/` | LCEL、ReAct、Hybrid 执行模式及工厂 |
| `agent/` | Agent 装配、短期记忆与模型/工具回调 |
| `chains/` | 目录审计和意图路由等固定流程 |
| `tools/` | 六个领域工具及统一注册入口 `registry.py` |
| `services/`、`search/` | 目录/系统底层服务与多搜索源网关 |
| `core/`、`config/` | 通用结构、配置、提示词与追踪 |
| `tests/` | 离线单元测试与多轮对话回归测试 |

旧的 `agent.tool_registry`、`chains.report_chain` 导入路径保留轻量兼容层；新代码分别使用 `tools.registry`、`cli.report`。

## 统一执行追踪

追踪默认开启，所有任务都追加写入同一个文件：

```text
logs/execution_trace.jsonl
```

每行是一个完整 JSON 事件，同一轮任务通过 `trace_id` 关联。交互模式下各轮共用 `session_id`，但每轮有独立的 `trace_id`。记录范围包括：

| 事件 | 记录内容 |
| :--- | :--- |
| `task.started/completed/failed` | 任务入参、最终结果和总耗时 |
| `mode.routed` | Hybrid 路由输入、执行器和目标任务 |
| `tool.completed/failed` | 每个领域工具的输入、输出和耗时 |
| `search_provider.*` | 每个搜索源的输入、结果数、错误和耗时 |
| `model.*` | ReAct 每轮模型输入、输出、Token 元数据和耗时 |
| `agent_tool.*` | Agent 选择工具时的参数和返回值 |

命令输出也会返回 `trace_id` 和 `trace_file`。查看某次任务：

```bash
grep '"trace_id":"<trace-id>"' logs/execution_trace.jsonl
```

默认 `payload_mode: sanitized`，会保留脱敏后的输入输出：API Key、Token、密码、Cookie 等字段被替换为 `<redacted>`，本机绝对路径被替换为 `<local_path>/文件名`。如果只想记录事件和耗时，可将 `tracing.payload_mode` 设为 `none`。

> [!WARNING]
> JSONL 为单文件持续追加，当前未做自动轮转。长期常驻运行时需由部署环境配置文件轮转或定期归档。

## 测试

```bash
PYTHONPYCACHEPREFIX=/tmp/langchain-jason-pycache \
  python3 -m unittest discover -s tests -v
```

## 安全边界

- 所有本地工具都是只读的，不包含删除、移动、重命名或修改功能。
- 拒绝扫描 `/` 和用户主目录本身，并校验解析后的路径仍在允许根目录内。
- 目录扫描不跟随符号链接。
- 搜索工具仅接收用户明确的公开查询，不会自动上传本地文件名、路径或内容。
- 重复文件检测只读取哈希，不自动删除任何文件。
