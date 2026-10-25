"""输入异常显式报告；修正必须通过配置或命令确认。"""
from dataclasses import replace
from pathlib import Path

from ..schemas import FORMATS, TYPES


def check_inputs(questions, cfg):
    issues, applied, normalized = [], [], []
    root = Path(cfg["paths"]["files"]).resolve()
    seen = set()
    fixes = cfg["corrections"] if cfg["run"]["corrections_enabled"] else {}
    for q in questions:
        fields = {}
        for name, mapping in (("file_name", "file_aliases"), ("question_type", "question_types")):
            key = q.file_name if name == "file_name" else q.id
            value = fixes.get(mapping, {}).get(key)
            if value is not None and value != getattr(q, name):
                fields[name] = value
                applied.append({"id": q.id, "field": name, "before": getattr(q, name), "after": value})
        q = replace(q, **fields)
        reasons = []
        if not q.id or q.id in seen:
            reasons.append("ID 为空或重复")
        seen.add(q.id)
        if not q.question.strip():
            reasons.append("题目为空")
        if q.question_type not in TYPES:
            reasons.append("非法 question_type")
        if q.answer_format and q.answer_format not in FORMATS:
            reasons.append("不支持的 answer_format，需显式扩展格式适配")
        path = (root / q.file_name).resolve()
        if not q.file_name or not path.is_relative_to(root) or path == root:
            reasons.append("非法文件路径")
        elif not path.is_file():
            reasons.append("关联文件不存在")
        elif path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
            reasons.append("不支持的文件格式")
        if reasons:
            issues.append({"id": q.id, "issues": reasons})
        normalized.append(q)
    if not questions:
        issues.append({"id": "", "issues": ["题目清单为空"]})
    return normalized, {"question_count": len(questions), "issues": issues, "corrections": applied,
                        "ok": not issues}
