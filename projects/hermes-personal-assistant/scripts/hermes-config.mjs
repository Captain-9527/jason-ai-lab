import { writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
process.loadEnvFile(".env");
const url =
  process.argv[2] ?? `http://127.0.0.1:${process.env.PORT ?? 8765}/mcp`;
new URL(url);
mkdirSync("data", { recursive: true });
const file = resolve("data/hermes-mcp.yaml");
writeFileSync(
  file,
  `mcp_servers:\n  personal_assistant:\n    url: ${JSON.stringify(url)}\n    headers:\n      Authorization: ${JSON.stringify("Bearer " + process.env.ASSISTANT_TOKEN)}\n`,
  { mode: 0o600 },
);
console.log(`已生成私有配置：${file}；仅合并该服务项到 Hermes，勿提交此文件。`);
