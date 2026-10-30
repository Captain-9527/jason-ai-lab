import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { resolve } from "node:path";
import { McpServer } from "@modelcontextprotocol/server";
import {
  NodeStreamableHTTPServerTransport,
  hostHeaderValidation,
} from "@modelcontextprotocol/node";
import { z } from "zod";
import { config, prepare } from "./config.js";
import { Store } from "./store.js";
import {
  collect,
  collectVideo,
  login,
  authStatus,
  closeBrowser,
} from "./browser.js";
prepare();
const store = new Store(resolve(config.dataDir, "assistant.sqlite"));
let tail: Promise<unknown> = Promise.resolve();
let shuttingDown = false;
function serial<T>(fn: () => Promise<T>): Promise<T> {
  const next = tail.then(fn, fn);
  tail = next.catch(() => {});
  return next;
}
const reply = (data: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(data) }],
});
const uid = z.string().regex(/^\d{1,20}$/);
function mcpServer() {
  const mcp = new McpServer({
    name: "hermes-personal-assistant",
    version: "0.1.0",
  });
  mcp.registerTool(
    "get_integration_status",
    {
      description: "查询运行状态；会话 Cookie 存在不等于登录有效",
      inputSchema: z.object({ check_browser: z.boolean().default(false) }),
    },
    async ({ check_browser }) =>
      reply({
        demo_mode: config.demo,
        subscriptions: store.subscriptions(),
        browser: check_browser ? await serial(authStatus) : "not_checked",
      }),
  );
  mcp.registerTool(
    "manage_subscriptions",
    {
      description: "管理 B站 UP主 UID 订阅",
      inputSchema: z.object({
        action: z.enum(["list", "add", "remove"]),
        uid: uid.optional(),
      }),
    },
    async (args) => {
      if (args.action !== "list" && !args.uid)
        return { ...reply({ error: "UID_REQUIRED" }), isError: true };
      if (args.action === "add") store.subscribe(args.uid!);
      if (args.action === "remove") store.unsubscribe(args.uid!);
      return reply(store.subscriptions());
    },
  );
  mcp.registerTool(
    "start_login",
    {
      description:
        "在服务所在电脑打开 B站登录窗口，由用户扫码；会等待当前采集结束",
      inputSchema: z.object({}),
    },
    async () => reply(await serial(login)),
  );
  mcp.registerTool(
    "request_content_sync",
    {
      description: "同步 B站订阅内容，返回任务ID；demo 仅在演示模式允许",
      inputSchema: z.object({
        source: z.enum(["bilibili", "demo"]).default("bilibili"),
        limit: z.number().int().min(1).max(50).default(20),
        video_url: z
          .string()
          .regex(/^https:\/\/www\.bilibili\.com\/video\/BV[0-9A-Za-z]{10}\/?$/)
          .optional(),
      }),
    },
    async ({ source, limit, video_url }) => {
      if (shuttingDown)
        return { ...reply({ error: "SHUTTING_DOWN" }), isError: true };
      if (source === "demo" && video_url)
        return {
          ...reply({ error: "DEMO_VIDEO_URL_NOT_ALLOWED" }),
          isError: true,
        };
      if (source === "demo" && !config.demo)
        return { ...reply({ error: "DEMO_DISABLED" }), isError: true };
      const subscriptions = store.subscriptions();
      if (source === "bilibili" && !video_url && !subscriptions.length)
        return {
          ...reply({ error: "NO_SUBSCRIPTIONS", message: "先添加 UP主 UID" }),
          isError: true,
        };
      const active = store.active(source);
      if (active) return reply({ job_id: active.id, reused: true });
      const id = store.createJob(source);
      void serial(async () => {
        store.update(id, "running");
        try {
          const items =
            source === "bilibili" && video_url
              ? await collectVideo(video_url)
              : [];
          if (!video_url)
            for (const sub of source === "demo"
              ? [{ uid: "demo" }]
              : subscriptions)
              items.push(...(await collect(sub.uid, limit, source === "demo")));
          store.save(items);
          store.update(id, "succeeded", {
            collected: items.length,
            source,
            coverage: "仅本次可见视频列表，不保证历史完整性",
          });
        } catch (error) {
          store.update(
            id,
            "failed",
            undefined,
            error instanceof Error ? error.message : "UNKNOWN_ERROR",
          );
        }
      });
      return reply({ job_id: id, reused: false });
    },
  );
  mcp.registerTool(
    "get_sync_status",
    {
      description: "查询同步任务；失败不代表没有更新",
      inputSchema: z.object({ job_id: z.string().uuid() }),
    },
    async ({ job_id }) => reply(store.job(job_id) ?? { error: "NOT_FOUND" }),
  );
  mcp.registerTool(
    "list_content",
    {
      description: "查询已采集内容；demo 是演示数据，不能当作真实平台更新",
      inputSchema: z.object({
        limit: z.number().int().min(1).max(100).default(20),
        source: z.enum(["bilibili", "demo"]).default("bilibili"),
      }),
    },
    async ({ limit, source }) => reply(store.list(limit, source)),
  );
  mcp.registerTool(
    "record_feedback",
    {
      description: "记录内容已读或兴趣偏好",
      inputSchema: z.object({
        content_id: z.string(),
        feedback: z.enum(["read", "liked", "disliked"]),
      }),
    },
    async ({ content_id, feedback }) =>
      reply({ updated: store.feedback(content_id, feedback) }),
  );
  return mcp;
}
const allowedHosts = (process.env.ALLOWED_HOSTS ?? "127.0.0.1,localhost,[::1]")
  .split(",")
  .map((s) => s.trim());
const validateHost = hostHeaderValidation(allowedHosts);
const server = createServer(async (req, res) => {
  try {
    if (!validateHost(req, res)) return;
    if (req.headers.origin) {
      res.writeHead(403).end("Browser origins are not allowed");
      return;
    }
    const expected = Buffer.from(`Bearer ${config.token}`);
    const actual = Buffer.from(req.headers.authorization ?? "");
    if (
      actual.length !== expected.length ||
      !timingSafeEqual(actual, expected)
    ) {
      res.writeHead(401).end("Unauthorized");
      return;
    }
    if (req.url === "/health") {
      res
        .writeHead(200, { "content-type": "application/json" })
        .end(
          JSON.stringify({ status: "ok", version: "0.1.0", pid: process.pid }),
        );
      return;
    }
    if (req.url !== "/mcp") {
      res.writeHead(404).end();
      return;
    }
    const mcp = mcpServer();
    const transport = new NodeStreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    res.on("close", () => {
      void mcp.close().catch(() => console.error("MCP close failed"));
    });
    await mcp.connect(transport);
    await transport.handleRequest(req, res);
  } catch (error) {
    console.error(
      "HTTP request failed:",
      error instanceof Error ? error.name : "Unknown",
    );
    if (!res.headersSent) res.writeHead(500);
    res.end();
  }
});
server.listen(config.port, config.host, () =>
  console.log(
    `MCP ready: http://${config.host}:${config.port}/mcp; demo=${config.demo}`,
  ),
);
server.on("error", (error) => {
  console.error(error.message);
  process.exitCode = 1;
  store.close();
});
async function stop() {
  if (shuttingDown) return;
  shuttingDown = true;
  server.close();
  await tail;
  await closeBrowser();
  store.close();
  process.exit(0);
}
process.on("SIGINT", () => void stop());
process.on("SIGTERM", () => void stop());
