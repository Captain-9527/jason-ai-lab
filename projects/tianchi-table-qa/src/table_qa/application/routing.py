"""可配置语义路由；不会读取文档名称、题号或答案。"""
import re


def classify(q, cfg):
    # 仅去除编号式文件标记，不能把它前面的中文题意一并吞掉。
    text = re.sub(r'[0-9A-Za-z_-]+\.(?:pdf|png|jpe?g|webp)(?![A-Za-z])', '', q.question, flags=re.I)
    if q.question_type == "structure":
        category, strategy, reason = "structure", "compare", "structure 类型"
    else:
        match = next((r for r in cfg["routes"]["rules"] if re.search(r["pattern"], text)
                      and not (r.get("exclude") and re.search(r["exclude"], text))), None)
        rule = match or cfg["routes"]["fallback"]
        category, strategy = rule["category"], rule["strategy"]
        reason = rule.get("pattern", "未匹配，默认 direct")
    if cfg["run"]["strategy"] != "auto":
        strategy = cfg["run"]["strategy"]
    if cfg["run"].get("single_pass", False) and strategy == "compare":
        strategy = "direct"
        reason += "；single_pass将compare收敛为一次原图求解"
    return {"category": category, "strategy": strategy, "reason": reason,
            "needs_tables": strategy in {"structured", "compare"}}
