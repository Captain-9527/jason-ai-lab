import { test } from "node:test";
import assert from "node:assert/strict";
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
