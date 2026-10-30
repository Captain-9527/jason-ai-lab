import { resolve } from "node:path";
import { mkdirSync } from "node:fs";
export const config = {
  host: process.env.HOST ?? "127.0.0.1",
  port: Number(process.env.PORT ?? 8765),
  token: process.env.ASSISTANT_TOKEN ?? "",
  dataDir: resolve(process.env.DATA_DIR ?? "./data"),
  demo: process.env.DEMO_MODE === "true",
  headless: process.env.HEADLESS !== "false",
};
export function prepare() {
  if (config.token.length < 24 || config.token.includes("replace-with"))
    throw new Error("先执行 npm run setup，配置至少 24 位 ASSISTANT_TOKEN");
  if (!Number.isInteger(config.port) || config.port < 1 || config.port > 65535)
    throw new Error("PORT 无效");
  mkdirSync(config.dataDir, { recursive: true });
  const tmp = resolve(".codex-tmp");
  mkdirSync(tmp, { recursive: true });
  process.env.TMPDIR = tmp;
  process.env.TEMP = tmp;
  process.env.TMP = tmp;
  process.env.PLAYWRIGHT_BROWSERS_PATH ??= resolve(config.dataDir, "browsers");
}
