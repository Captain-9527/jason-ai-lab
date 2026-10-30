import { spawn } from "node:child_process";
import {
  mkdirSync,
  openSync,
  closeSync,
  writeFileSync,
  readFileSync,
  existsSync,
  unlinkSync,
} from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "..");
process.chdir(root);
process.loadEnvFile(".env");
const data = resolve(process.env.DATA_DIR ?? "data");
mkdirSync(data, { recursive: true });
const pidFile = resolve(data, "service.pid");
const url = `http://127.0.0.1:${process.env.PORT ?? 8765}/health`;
async function health() {
  try {
    const r = await fetch(url, {
      headers: { Authorization: `Bearer ${process.env.ASSISTANT_TOKEN}` },
      signal: AbortSignal.timeout(1500),
    });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
const action = process.argv[2] ?? "status";
if (action === "start") {
  if (await health()) {
    console.log("服务已经运行");
    process.exit(0);
  }
  const fd = openSync(resolve(data, "service.log"), "a");
  const child = spawn(process.execPath, ["--env-file=.env", "dist/server.js"], {
    cwd: root,
    detached: true,
    stdio: ["ignore", fd, fd],
    env: process.env,
  });
  closeSync(fd);
  child.unref();
  writeFileSync(pidFile, String(child.pid));
  for (let i = 0; i < 30; i++) {
    const h = await health();
    if (h && h.pid === child.pid) {
      console.log(
        `服务已启动：http://127.0.0.1:${process.env.PORT ?? 8765}/mcp`,
      );
      process.exit(0);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  console.error("启动失败，检查 data/service.log");
  process.exit(1);
} else if (action === "stop") {
  const h = await health();
  const pid = existsSync(pidFile)
    ? Number(readFileSync(pidFile, "utf8"))
    : null;
  if (!h || h.pid !== pid) {
    console.error("无法确认当前服务进程，未发送停止信号");
    process.exit(1);
  }
  process.kill(pid, "SIGTERM");
  unlinkSync(pidFile);
  console.log("已请求停止服务");
} else {
  const h = await health();
  console.log(h ?? { status: "stopped" });
  if (!h) process.exitCode = 1;
}
