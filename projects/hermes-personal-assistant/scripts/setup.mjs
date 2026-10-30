import { mkdirSync, existsSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { resolve } from "node:path";
const tmp = resolve(".codex-tmp");
mkdirSync(tmp, { recursive: true });
mkdirSync("data", { recursive: true });
if (!existsSync(".env")) {
  writeFileSync(
    ".env",
    `HOST=127.0.0.1\nPORT=8765\nASSISTANT_TOKEN=${randomBytes(32).toString("hex")}\nDATA_DIR=./data\nDEMO_MODE=false\nHEADLESS=true\nTMPDIR=${tmp}\nTEMP=${tmp}\nTMP=${tmp}\nPLAYWRIGHT_BROWSERS_PATH=${resolve("data/browsers")}\n`,
    { mode: 0o600 },
  );
}
console.log("本地配置已准备；密钥保存在 .env，未输出。");
