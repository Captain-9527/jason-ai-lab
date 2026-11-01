import { test } from "node:test";
import assert from "node:assert/strict";
import Database from "better-sqlite3";
import { Store } from "../store.js";
import { mkdirSync, mkdtempSync } from "node:fs";
import { resolve } from "node:path";
test("重复采集保留反馈；订阅去重；参数化 SQL", () => {
  const s = new Store(":memory:");
  const item = {
    id: "bilibili:BV1",
    title: "a' title",
    url: "https://www.bilibili.com/video/BV1",
    author: "1",
    source: "bilibili",
  };
  s.save([item]);
  s.feedback(item.id, "read");
  s.save([{ ...item, title: "updated" }]);
  const rows = s.list() as { title: string; feedback: string }[];
  assert.equal(rows.length, 1);
  assert.equal(rows[0].title, "updated");
  assert.equal(rows[0].feedback, "read");
  s.subscribe("123");
  s.subscribe("123");
  assert.equal(s.subscriptions().length, 1);
  s.close();
});
test("重启将未完成任务标记 interrupted，保留内容", () => {
  mkdirSync(".codex-tmp", { recursive: true });
  const path = resolve(mkdtempSync(".codex-tmp/store-"), "test.sqlite");
  let s = new Store(path);
  const id = s.createJob("demo");
  s.update(id, "running");
  s.close();
  s = new Store(path);
  assert.equal((s.job(id) as { status: string }).status, "interrupted");
  s.close();
});

test("不同请求不会复用同源任务，已完成任务允许重新同步", () => {
  const s = new Store(":memory:");
  const first = s.createJob("bilibili", "video-a");
  assert.equal(s.active("bilibili", "video-a")?.id, first);
  assert.equal(s.active("bilibili", "video-b"), undefined);
  const second = s.createJob("bilibili", "video-b");
  assert.notEqual(first, second);
  s.update(first, "succeeded", { collected: 1 });
  assert.equal(s.active("bilibili", "video-a"), undefined);
  assert.equal(s.active("bilibili", "video-b")?.id, second);
  s.close();
});

test("v1 数据库增量迁移保留历史任务", () => {
  mkdirSync(".codex-tmp", { recursive: true });
  const path = resolve(mkdtempSync(".codex-tmp/migration-"), "test.sqlite");
  const old = new Database(path);
  old.exec(`CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, result TEXT, error TEXT);
    INSERT INTO jobs (id,source,status,created_at) VALUES ('history','bilibili','succeeded','2026-10-30');
    PRAGMA user_version = 1;`);
  old.close();
  const s = new Store(path);
  assert.equal(s.db.pragma("user_version", { simple: true }), 2);
  assert.equal((s.job("history") as { status: string }).status, "succeeded");
  const id = s.createJob("bilibili", "new-key");
  assert.equal(s.active("bilibili", "new-key")?.id, id);
  s.close();
});
