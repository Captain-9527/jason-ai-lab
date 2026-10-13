# Vanna Jason

白酒营销场景的 Text-to-SQL 学习项目：Vanna 检索训练知识、通义千问生成 SQL、MySQL 查询、Plotly 本地图表，以及高德地图 / Tavily MCP 问答。

## 环境与启动

使用 Python 3.11、MySQL 8.0。MCP 功能另需 Node.js 与 `npx`（建议 Node.js 20+）。

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python restore_snapshot.py
```

先在自己的本地 MySQL 导入 `examples/demo.sql`（使用具备建库权限的管理账号，密码通过提示输入），再为应用单独配置**仅有 vanna_demo 库 SELECT 权限**的账号。脚本只用于新的演示环境，不应在生产库执行；重复导入遇到已有表会失败。

在 `.env` 填写 `DB_USER`、`DB_PASSWORD`、`DASHSCOPE_API_KEY`，然后启动：

```sh
python app_gradio.py
```

打开 http://127.0.0.1:7860 。旧版 Flask 入口为 `python app.py`，端口 8084。两个入口均仅监听本机，没有面向公网的身份认证，勿直接暴露到网络。

MCP 标签页另外需要 `AMAP_MAPS_API_KEY` 与 `TAVILY_API_KEY`；首次运行 `npx` 会下载第三方 MCP Server，并将所配置的工具密钥传给对应进程。仅配置并使用自己信任的服务。普通 SQL 分析不需要这些工具密钥。

## 训练成果与数据

- `examples/chroma_db/`：保留的原始 Chroma 训练快照，约 7 MB。Vanna 可读取 **24 条有效训练记录**（13 条 SQL 示例、11 条文档）；SQLite 内部另有历史残留记录，不等于有效训练条数。
- `restore_snapshot.py`：复制到被 Git 忽略的 `chroma_db/`，拒绝覆盖已有目录。运行、追加训练只操作本地副本，不要将 `CHROMA_PATH` 指向发布快照。
- `train_vanna.py`：手工 DDL、业务说明与 SQL 示例；`python train_vanna.py` 追加训练，`--reset` 会清空本地训练成果，使用前备份。`--from-db` 会读取所配置数据库的表结构。
- `examples/demo.sql`：两张演示表及完全虚构的示例数据，不含真实客户、扫码明细或数据库账号。

快照保留原学习场景的表结构、业务规则、历史数据库名称和统计描述，**不是完全合成或完全匿名的数据集，也不是 MySQL 业务数据库备份**。发布前已与原 `.env` 的账号、密码、地址、API Key 做精确匹配检查。若将私有仓库改为公开，仍应另行确认这些业务资料的公开权限。

快照中的旧数据库名称与示例统计不代表演示库。遇到模型使用旧库名前缀时，应改用 `vanna_demo` 或根据演示库重新训练；模拟数据量也不会复现原业务统计。Chroma 默认嵌入模型在首次检索/重新训练时可能下载，需网络与本地缓存。

## 安全边界

- 不附带原 `.env`，不内置原数据库地址和账号。真实配置只放本地 `.env`。
- SQL 经单语句 SELECT 校验，并在 MySQL 只读事务内执行。校验不是完整 SQL 沙箱，必须配合专用最小权限账号；尚未实现查询超时和结果行数硬限制，仅用于受信任的本地学习。
- 不执行模型生成的 Python；图表使用固定规则，显示最多 100 行中的首个数值列。
- 默认 `ALLOW_LLM_TO_SEE_DATA=false`，不让模型查看中间查询数据或生成结果摘要；问题及检索到的 DDL、业务文档和 SQL 示例仍会发送至 DashScope。设为 `true` 意味着允许发送查询结果。MCP 问答会向模型和工具服务发送相关提问。
- `CHROMA_COLLECTION` 保留原配置接口；Vanna 0.7.9 实际使用 `sql`、`ddl`、`documentation` 三个集合，不应依靠此变量隔离不同项目，应使用不同 `CHROMA_PATH`。

## 验证记录

已在本机已有 Python 3.11 环境完成编译、3 项单元测试（含多组 SQL 拒绝用例）、13 条训练 SQL 校验、临时副本的快照读取，以及 Gradio / Flask 模块导入。

```sh
python -m unittest -v test_safety
python -m compileall -q .
```

本机测试出现 PyArrow 沙箱硬件探测警告和 Chroma 遥测兼容性警告，未阻断上述检查。未验证全新环境安装、MySQL 导入/只读事务、真实模型请求及 MCP 联调；依赖文件并非完整锁文件。没有连接原业务数据库进行验证。
