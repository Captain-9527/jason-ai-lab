import { chromium, type BrowserContext } from "playwright";
import { resolve } from "node:path";
import { config } from "./config.js";
import type { Item } from "./store.js";
let context: BrowserContext | undefined;
export async function openBrowser(headless = config.headless) {
  if (!context) {
    context = await chromium.launchPersistentContext(
      resolve(config.dataDir, "browser-profile"),
      {
        headless,
        ...(process.env.BROWSER_CHANNEL
          ? { channel: process.env.BROWSER_CHANNEL }
          : {}),
        viewport: { width: 1280, height: 900 },
      },
    );
    context.setDefaultTimeout(15000);
    await context.route("**/*", (route) =>
      route.request().resourceType() === "media"
        ? route.abort()
        : route.continue(),
    );
    context.on("close", () => {
      context = undefined;
    });
  }
  return context;
}
export async function closeBrowser() {
  await context?.close();
  context = undefined;
}
export async function login() {
  await closeBrowser();
  const ctx = await openBrowser(false);
  const page = await ctx.newPage();
  await page.goto("https://passport.bilibili.com/login", {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  return {
    status: "awaiting_user",
    message:
      "请在本机浏览器完成登录；随后调用 request_content_sync 验证页面可访问性。",
  };
}
export async function authStatus() {
  const ctx = await openBrowser();
  const cookies = await ctx.cookies("https://www.bilibili.com");
  return {
    status: cookies.some((c) => c.name === "SESSDATA")
      ? "session_present_unverified"
      : "login_required",
    message: "Cookie 存在不代表服务端会话有效；采集结果另行验证。",
  };
}
export async function collect(
  uid: string,
  limit: number,
  demo = false,
): Promise<Item[]> {
  const ctx = await openBrowser();
  const page = await ctx.newPage();
  try {
    if (demo)
      await page.setContent(
        `<html><body><a href="https://www.bilibili.com/video/BV1xx411c7mD" title="演示视频：MCP 调用验证">演示视频</a><a href="https://www.bilibili.com/video/BV1xx411c7mD">重复</a><a href="https://www.bilibili.com/video/BV1yy411c7mE" title="演示视频：去重验证">演示视频</a></body></html>`,
      );
    else {
      await page.goto(`https://space.bilibili.com/${uid}/upload/video`, {
        waitUntil: "domcontentloaded",
        timeout: 45000,
      });
      await page
        .locator('a[href*="/video/BV"]')
        .first()
        .waitFor({ timeout: 20000 })
        .catch(async () => {
          const text = await page.locator("body").innerText();
          if (/验证码|安全验证|访问受限|异常请求/.test(text))
            throw new Error("PLATFORM_CHALLENGE：请在浏览器处理验证后重试");
          if (/扫码登录|登录后查看/.test(text))
            throw new Error("LOGIN_REQUIRED：请重新登录");
          throw new Error(
            "CONTENT_UNAVAILABLE：页面无可读取视频，可能无投稿、布局变更或加载失败",
          );
        });
      // 限定加载量，避免无限滚动。
      for (let i = 0; i < 2; i++) {
        await page.mouse.wheel(0, 800);
        await page.waitForTimeout(600);
      }
    }
    const links = await page
      .locator('a[href*="/video/BV"]')
      .evaluateAll((nodes) =>
        nodes.map((n) => ({
          url: (n as HTMLAnchorElement).href,
          title: n.getAttribute("title") || n.textContent?.trim() || "",
        })),
      );
    const items = new Map<string, Item>();
    for (const link of links) {
      const id = link.url.match(/\/video\/(BV[0-9A-Za-z]+)/)?.[1];
      if (id && link.title && !items.has(id))
        items.set(id, {
          id: `${demo ? "demo" : "bilibili"}:${id}`,
          title: link.title,
          url: `https://www.bilibili.com/video/${id}`,
          author: demo ? "演示账号" : uid,
          source: demo ? "demo" : "bilibili",
        });
    }
    if (items.size === 0)
      throw new Error("CONTENT_UNAVAILABLE：未提取到内容，未判定为没有更新");
    return [...items.values()].slice(0, limit);
  } finally {
    await page.close();
  }
}

export async function collectVideo(url: string): Promise<Item[]> {
  const match = url.match(
    /^https:\/\/www\.bilibili\.com\/video\/(BV[0-9A-Za-z]{10})(?:\/)?$/,
  );
  if (!match) throw new Error("INVALID_VIDEO_URL");
  const canonical = `https://www.bilibili.com/video/${match[1]}`;
  const ctx = await openBrowser();
  const page = await ctx.newPage();
  try {
    await page.goto(canonical, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    const title = (
      await page.locator("h1").first().innerText({ timeout: 20000 })
    ).trim();
    if (!title) throw new Error("CONTENT_UNAVAILABLE：缺少视频标题");
    const author = await page
      .locator(".up-name")
      .first()
      .textContent({ timeout: 3000 })
      .catch(() => null);
    return [
      {
        id: `bilibili:${match[1]}`,
        title,
        url: canonical,
        author: author?.trim() ?? "unknown",
        source: "bilibili",
      },
    ];
  } finally {
    await page.close();
  }
}
