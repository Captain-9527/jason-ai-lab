# Jason AI Lab 🧠

> Learning, experimenting, and building with AI.

这里记录我的 AI 学习笔记、技术实验与实践项目。

## 学习方向

- 大语言模型（LLM）
- Prompt Engineering
- 检索增强生成（RAG）
- AI Agent
- 模型评估与应用部署

## 内容导航

| 目录 | 内容 |
| --- | --- |
| [`notes/`](notes/) | 学习笔记与开源项目解读 |
| [`projects/`](projects/) | 可运行项目、实验与原型 |
| [`resources/README.md`](resources/README.md) | 课程、论文和工具链接 |

## 开源项目解读

| 文章 | 分析版本 |
| --- | --- |
| [Hermes Agent 架构设计](notes/source-code-analysis/hermes-agent.md) | v0.16.0 |
| [nanobot 架构设计](notes/source-code-analysis/nanobot.md) | v0.2.1 |
| [Hermes Agent 与 nanobot 对比](notes/source-code-analysis/agent-comparison.md) | 对应上述版本 |

以上为个人学习资料，版本信息沿用原文，不代表上游项目当前状态。

## 项目进度

| 项目 | 简介 | 状态 |
| --- | --- | --- |
| [LangChain Jason](projects/langchain-jason/) | LCEL、ReAct 与混合路由的只读 macOS 目录分析 Agent | 已完成 |

## 使用约定

- 笔记使用 Markdown 编写，并尽量记录日期、结论、示例和参考资料。
- 每个项目应包含独立的 README、依赖说明和运行方法。
- 开源项目解读先在专题内平铺，多篇文章属于同一项目时再建立子目录。
- 图片存放在所属文章附近的 `assets/`；同一份内容只保留一个维护位置，其他位置通过链接引用。
- 项目设计文档随项目保存在 `docs/`，实验与原型通过项目状态标注。
- API Key、令牌、账号信息、私有数据及模型权重不得提交到仓库。
- 环境变量通过 `.env` 管理，并提供不含敏感值的 `.env.example`。
