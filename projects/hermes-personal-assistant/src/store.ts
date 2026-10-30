import Database from "better-sqlite3";
import { randomUUID } from "node:crypto";
export type Item = {
  id: string;
  title: string;
  url: string;
  author: string;
  source: string;
};
export class Store {
  db: Database.Database;
  constructor(path: string) {
    this.db = new Database(path);
    this.db.pragma("journal_mode = WAL");
    this.db
      .exec(`CREATE TABLE IF NOT EXISTS subscriptions (id TEXT PRIMARY KEY, uid TEXT UNIQUE NOT NULL);
      CREATE TABLE IF NOT EXISTS content (id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, author TEXT NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL, feedback TEXT);
      CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, result TEXT, error TEXT);
      PRAGMA user_version = 1;`);
    // 进程中断的任务显式失败，避免把未知结果伪装成成功。
    this.db
      .prepare(
        "UPDATE jobs SET status='interrupted', error='服务重启，请重新同步' WHERE status IN ('queued','running')",
      )
      .run();
  }
  subscribe(uid: string) {
    this.db
      .prepare("INSERT OR IGNORE INTO subscriptions VALUES (?,?)")
      .run(randomUUID(), uid);
    return this.subscriptions();
  }
  subscriptions() {
    return this.db.prepare("SELECT * FROM subscriptions").all() as {
      id: string;
      uid: string;
    }[];
  }
  unsubscribe(uid: string) {
    return this.db.prepare("DELETE FROM subscriptions WHERE uid=?").run(uid)
      .changes;
  }
  save(items: Item[]) {
    const stmt = this.db.prepare(
      "INSERT INTO content (id,title,url,author,source,fetched_at) VALUES (@id,@title,@url,@author,@source,@at) ON CONFLICT(id) DO UPDATE SET title=excluded.title,author=excluded.author,fetched_at=excluded.fetched_at",
    );
    this.db.transaction(() => {
      for (const item of items)
        stmt.run({ ...item, at: new Date().toISOString() });
    })();
  }
  list(limit = 20, source?: string) {
    return source
      ? this.db
          .prepare(
            "SELECT * FROM content WHERE source=? ORDER BY fetched_at DESC,id LIMIT ?",
          )
          .all(source, limit)
      : this.db
          .prepare("SELECT * FROM content ORDER BY fetched_at DESC,id LIMIT ?")
          .all(limit);
  }
  feedback(id: string, value: string) {
    return this.db
      .prepare("UPDATE content SET feedback=? WHERE id=?")
      .run(value, id).changes;
  }
  createJob(source: string) {
    const id = randomUUID();
    this.db
      .prepare(
        "INSERT INTO jobs (id,source,status,created_at) VALUES (?,?,'queued',?)",
      )
      .run(id, source, new Date().toISOString());
    return id;
  }
  update(id: string, status: string, result?: unknown, error?: string) {
    this.db
      .prepare(
        "UPDATE jobs SET status=?,result=?,error=?,finished_at=? WHERE id=?",
      )
      .run(
        status,
        result ? JSON.stringify(result) : null,
        error ?? null,
        status === "running" ? null : new Date().toISOString(),
        id,
      );
  }
  job(id: string) {
    return this.db.prepare("SELECT * FROM jobs WHERE id=?").get(id);
  }
  active(source: string) {
    return this.db
      .prepare(
        "SELECT id FROM jobs WHERE source=? AND status IN ('queued','running') LIMIT 1",
      )
      .get(source) as { id: string } | undefined;
  }
  close() {
    this.db.close();
  }
}
