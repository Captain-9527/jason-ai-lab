"""内容寻址缓存与原子写入；不隐吞缓存损坏。"""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def atomic_text(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_results(run) -> dict:
    path = Path(run) / "results.jsonl"
    if not path.exists():
        return {}
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("结果文件存在重复 ID")
    return {r["id"]: r for r in records}


def save_results(run, results: dict) -> None:
    atomic_text(Path(run) / "results.jsonl", "".join(
        json.dumps(v, ensure_ascii=False, allow_nan=False) + "\n" for v in results.values()))
    atomic_json(Path(run) / "checkpoint.json", {k: v["status"] for k, v in results.items()})


class Cache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def get(self, key):
        path = self.directory / f"{key}.json"
        return read_json(path) if path.exists() else None

    def put(self, key, value):
        atomic_json(self.directory / f"{key}.json", value)
