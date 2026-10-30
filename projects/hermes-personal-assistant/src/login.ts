import {
  Client,
  StreamableHTTPClientTransport,
} from "@modelcontextprotocol/client";
import { config } from "./config.js";
const client = new Client({ name: "assistant-login", version: "0.1.0" });
try {
  await client.connect(
    new StreamableHTTPClientTransport(
      new URL(process.env.MCP_URL ?? `http://127.0.0.1:${config.port}/mcp`),
      { requestInit: { headers: { Authorization: `Bearer ${config.token}` } } },
    ),
  );
  const result = await client.callTool({ name: "start_login", arguments: {} });
  console.log(JSON.stringify(result));
  if (result.isError) process.exitCode = 1;
} finally {
  await client.close();
}
