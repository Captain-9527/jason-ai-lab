# Hermes Personal Assistant

个人自用的浏览器内容采集 MCP 服务。macOS 开发、调用验证，Windows 原生部署浏览器，WSL Hermes 经 Streamable HTTP 连接。

当前实现：B站 UP主 UID 订阅、独立浏览器登录目录、视频列表提取、SQLite 去重、异步任务、反馈、MCP 鉴权和 CLI 调用。尚未实现抖音、完整关注流、视频全文摘要、时间规划、画像与赛事。

## 环境

Node.js 22.22.2、TypeScript 7.0.2、Playwright 1.64.1、MCP server/client 2.4.0、Node adapter 2.1.2、Zod 4.6.7、better-sqlite3 13.0.3。以 package-lock.json 为实际依赖依据。浏览器版本由 Playwright 锁定；各操作系统分别安装。

## macOS 部署

在本项目目录运行：

```sh
mkdir -p .codex-tmp
TMPDIR="$PWD/.codex-tmp" npm ci
npm run setup
npm run build
npm run browser:install
npm test
npm run service:start
npm run smoke
```

`npm run setup` 只在 `.env` 不存在时创建配置，生成独立随机密钥。运行数据、认证状态、日志在 `data/`；临时文件在 `.codex-tmp/`，均已被 Git 忽略。

如果下载浏览器较慢，可在 `.env` 设置 `BROWSER_CHANNEL=chrome` 使用本机已安装 Google Chrome；省略浏览器下载步骤。此模式需自行维护 Chrome 版本兼容性。

服务地址：`http://127.0.0.1:8765/mcp`。HTTP 入口和 `/health` 都要求 `Authorization: Bearer <ASSISTANT_TOKEN>`；任意浏览器 Origin 被拒绝。MCP 为无状态 HTTP，业务任务状态存入 SQLite。

```sh
npm run service:status
npm run login
npm run smoke -- video https://www.bilibili.com/video/BV1xx411c7mD
npm run smoke -- live <你选择的UP主UID>
npm run service:stop
```

通用调用：

```sh
npm run call -- list_content '{"source":"bilibili","limit":5}'
npm run call -- manage_subscriptions '{"action":"add","uid":"你的UID"}'
```

`request_content_sync` 也支持 `video_url`，限定为 `https://www.bilibili.com/video/BV…` 规范链接，适合导入单视频。不能据此声称订阅列表采集已通过。

`login` 在服务所在电脑打开可见浏览器，由用户手动登录。采集会话与日常 Chrome 隔离。CLI `live` 会保存传入的订阅 UID，然后同步所有已配置订阅。无须为公开页面伪造登录，但平台可能要求验证。

## 可重复演示

演示使用真正的 Chromium 和 MCP 调用，但内容是本地 fixture，明确标记为 `source=demo`，不代表平台接入成功。

停止服务，将 `.env` 的 `DEMO_MODE` 改为 `true`，启动服务，再执行：

```sh
npm run smoke -- demo
npm run smoke -- demo
```

两次演示应只保存两条去重内容。完成后恢复 `DEMO_MODE=false` 并重启。真实查询默认只返回 `bilibili`，不混入演示数据。

## MCP 工具

| 工具 | 作用 |
| --- | --- |
| get_integration_status | 状态和订阅；可选检测 Cookie，但不声称会话有效 |
| manage_subscriptions | list/add/remove，uid 为数字字符串 |
| start_login | 串行打开本机登录窗口 |
| request_content_sync | 返回 job_id；同源执行中任务复用，浏览器串行采集 |
| get_sync_status | queued/running/succeeded/failed/interrupted |
| list_content | 按 source 查询，默认 bilibili |
| record_feedback | read/liked/disliked |

任务执行失败保留原因。未找到视频不会伪装为“零更新”。当前只采集本次加载的有限视频列表，作者字段是 UID，fetched_at 是采集时间；没有可靠发布时间时不编造。重复采集更新元数据，保留反馈。进程重启将未完成任务标记 interrupted，需重试。

## Hermes 接入

`npm run config:hermes` 可生成含本机密钥的 `data/hermes-mcp.yaml`（已忽略）。Windows 上可用 `npm run config:hermes -- http://<Windows主机IP>:8765/mcp` 生成 WSL 接入配置。

合并下面配置到实际使用 profile 的 config.yaml，不覆盖已有飞书或其他 MCP 配置：

```yaml
mcp_servers:
  personal_assistant:
    url: "http://127.0.0.1:8765/mcp"
    headers:
      Authorization: "Bearer <从服务.env读取ASSISTANT_TOKEN>"
```

将 `skills/personal-digest/` 复制到 Hermes 当前 profile 的技能目录。重载 MCP 或重启 Hermes 后，先要求列出工具，再发送“查看订阅”“添加 B站 UP主 UID …”“同步并汇总订阅更新”。飞书定时推送需要在目标 Hermes 上另外配置和实测，不能由 CLI smoke 成功推断已联通。

## Windows 迁移

1. 用 Git 同步源码，不复制 `node_modules`、`.env`、浏览器 profile 或 macOS Chromium。
2. Windows 原生 PowerShell 执行 `./scripts/install-windows.ps1`。
3. 执行 `./scripts/start-windows.ps1`，随后 `npm run smoke`。
4. 执行 `npm run login`，在 Windows 上重新登录。
5. 若 WSL 镜像网络支持 localhost，先试 `127.0.0.1`；否则使用 WSL 可达的 Windows 主机地址。需要在 Windows `.env` 设置 `HOST` 为具体接口或 `0.0.0.0`，设置 `ALLOWED_HOSTS=127.0.0.1,localhost,<Windows主机IP>` 并重启。防火墙只允许所需 WSL 来源，不开放公网。
6. Hermes 配置 Windows 地址与新密钥，验证工具调用和飞书回复。

自启动建议用 Windows 任务计划程序，在用户登录时调用 `scripts/start-windows.ps1`，采用“仅用户登录时运行”，保证扫码窗口可交互。机器常开但未登录不等于有可交互桌面。保持防休眠；WSL Hermes 自启动按原部署维护。

数据库只存 Windows 本地盘，避免 WSL 与 Windows 同时打开同一文件。需要迁移内容时先停止服务，使用 SQLite 一致性备份，不能在写入时只复制主数据库而遗漏 WAL。

## 验证与维护

`npm test` 验证去重、反馈保留和重启恢复；`npm run smoke` 验证 HTTP 鉴权、MCP 握手与工具发现；`smoke -- demo` 验证浏览器→SQLite→MCP；`smoke -- live UID` 单独验证真实平台。

日志：`data/service.log`。平台验证、布局变更或超时需要人工检查；不执行验证码绕过。一个服务实例拥有一个浏览器 profile 和数据库，禁止同数据目录启动多个实例。依赖升级必须同时更新锁文件、浏览器并重跑测试。

## 本次验证记录（2026-10-30）

- macOS / Node.js 22.22.2 / 已安装 Google Chrome（BROWSER_CHANNEL=chrome）：构建和 2 项存储测试通过。
- HTTP MCP：鉴权、Origin 拒绝、非法参数、新协议及 2025-03-26 初始化通过。
- 演示：真实浏览器读取本地 fixture，连续两次采集后仍为 2 条记录。
- 真实单视频：BV1xx411c7mD 提取“字幕君交流场所”、作者“碧诗”、原始链接，并通过 MCP 查询成功。
- UP主列表：UID 2 页面展示投稿数量但未返回视频条目，同步明确失败；该能力仍待登录及更多账号验证，不能视作完成。
- Skill frontmatter 校验通过。
- Windows 安装/常驻、目标 WSL Hermes 实际调用、飞书主动简报尚未执行。

本机已关闭演示模式，测试订阅已移除。实际视频样例和演示数据保存在本机忽略目录，不进入 Git。
