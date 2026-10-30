import {
  Client,
  StreamableHTTPClientTransport,
} from "@modelcontextprotocol/client";
import { config } from "./config.js";
const name = process.argv[2];
if (!name) throw new Error("用法：npm run call -- <tool_name> [JSON参数]");
const client = new Client({ name: "assistant-cli", version: "0.1.0" });
try {
  const args = JSON.parse(process.argv[3] ?? "{}") as Record<string, unknown>;
  await client.connect(
    new StreamableHTTPClientTransport(
      new URL(process.env.MCP_URL ?? `http://127.0.0.1:${config.port}/mcp`),
      { requestInit: { headers: { Authorization: `Bearer ${config.token}` } } },
    ),
  );
  const result = await client.callTool({ name, arguments: args });
  for (const item of result.content as { type: string; text?: string }[])
    if (item.type === "text") console.log(item.text);
  if (result.isError) process.exitCode = 1;
} finally {
  await client.close();
}
