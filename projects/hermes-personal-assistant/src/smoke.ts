import {
  Client,
  StreamableHTTPClientTransport,
} from "@modelcontextprotocol/client";
import assert from "node:assert/strict";
import { config } from "./config.js";
const base = process.env.MCP_URL ?? `http://127.0.0.1:${config.port}/mcp`;
const client = new Client({ name: "assistant-smoke", version: "0.1.0" });
const transport = new StreamableHTTPClientTransport(new URL(base), {
  requestInit: { headers: { Authorization: `Bearer ${config.token}` } },
});
async function call(name: string, args: Record<string, unknown> = {}) {
  const result = await client.callTool({ name, arguments: args });
  assert.ok(!result.isError, JSON.stringify(result));
  const blocks = result.content as { type: string; text?: string }[];
  return JSON.parse(blocks.find((x) => x.type === "text")!.text!);
}
try {
  assert.equal((await fetch(base)).status, 401);
  const auth = { Authorization: `Bearer ${config.token}` };
  assert.equal(
    (
      await fetch(base, {
        headers: { ...auth, Origin: "https://untrusted.example" },
      })
    ).status,
    403,
  );
  const legacy = await fetch(base, {
    method: "POST",
    headers: {
      ...auth,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "legacy-smoke", version: "1" },
      },
    }),
  });
  assert.equal(legacy.status, 200);
  const negotiated = (await legacy.json()) as {
    result?: { protocolVersion: string };
  };
  assert.equal(negotiated.result?.protocolVersion, "2025-03-26");
  await client.connect(transport);
  const tools = await client.listTools();
  assert.equal(tools.tools.length, 7);
  let rejected = false;
  try {
    const invalid = await client.callTool({
      name: "list_content",
      arguments: { limit: 1000 },
    });
    rejected = Boolean(invalid.isError);
  } catch {
    rejected = true;
  }
  assert.ok(rejected, "非法参数必须被拒绝");
  const mode = process.argv[2] ?? "status";
  console.log(
    "MCP handshake (current + 2025-03-26) / tools / auth / origin / validation: PASS",
  );
  if (mode === "demo" || mode === "live" || mode === "video") {
    const source = mode === "demo" ? "demo" : "bilibili";
    if (mode === "live" && process.argv[3])
      await call("manage_subscriptions", {
        action: "add",
        uid: process.argv[3],
      });
    const job = await call("request_content_sync", {
      source,
      limit: 5,
      ...(mode === "video" ? { video_url: process.argv[3] } : {}),
    });
    let state;
    for (let i = 0; i < 120; i++) {
      state = await call("get_sync_status", { job_id: job.job_id });
      if (!["queued", "running"].includes(state.status)) break;
      await new Promise((r) => setTimeout(r, 1000));
    }
    assert.equal(state.status, "succeeded", JSON.stringify(state));
    const items = await call("list_content", { source, limit: 20 });
    assert.ok(items.length > 0);
    console.log(
      `${source} browser -> SQLite -> MCP: PASS (${items.length} items)`,
    );
  } else console.log(await call("get_integration_status"));
} finally {
  await client.close();
}
