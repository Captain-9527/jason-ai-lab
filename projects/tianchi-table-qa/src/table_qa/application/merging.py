"""离线合并运行结果；不初始化模型，不修改来源目录中的答案。"""
import copy
import json
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..infrastructure.excel import export_submission
from ..infrastructure.storage import atomic_json, digest, file_hash, read_json, read_results, save_results
from ..schemas import PipelineError, Question
from ..validation.answer import format_answer
from .runner import run_lock, run_path


def export_policy(cfg):
    return {"answer": cfg.get("answer", {"preserve_decimal_zeros": False}),
            "fill_missing_answers": cfg["run"].get("fill_missing_answers", False),
            "submit_uncertain": cfg["run"].get("submit_uncertain", False),
            "submit_unlocalized": cfg["run"].get("submit_unlocalized", False)}


def merge_runs(cfg, run_ids, prefer_run=None):
    if len(run_ids) < 2 or len(set(run_ids)) != len(run_ids):
        raise ValueError("merge 需要至少两个不同运行编号")
    if prefer_run is not None and prefer_run not in run_ids:
        raise ValueError("prefer-run 必须属于待合并运行")
    folders = {rid: run_path(cfg, rid) for rid in run_ids}
    snapshots, records = {}, {}
    # 同时锁定所有来源，防止读取一半新一半旧的运行状态。
    with ExitStack() as stack:
        for rid in sorted(folders):
            stack.enter_context(run_lock(folders[rid]))
        for rid, folder in folders.items():
            snapshots[rid] = read_json(folder / "snapshot.json")
            records[rid] = read_results(folder)
    reference = snapshots[run_ids[0]]
    policy = export_policy(reference["config"])
    for snap in snapshots.values():
        if digest(snap["dataset"]) != snap["dataset_hash"] or snap["dataset_hash"] != reference["dataset_hash"]:
            raise ValueError("数据集指纹不同或快照损坏，不能合并")
        if snap["template_hash"] != reference["template_hash"]:
            raise ValueError("提交模板不同，不能合并")
        if export_policy(snap["config"]) != policy:
            raise ValueError("导出策略不同，不能静默合并，请统一策略后重新生成运行")
    if file_hash(cfg["paths"]["template"]) != reference["template_hash"]:
        raise ValueError("当前提交模板与来源运行不一致")
    questions = [Question(**q) for q in reference["dataset"]["questions"]]
    ids = {q.id for q in questions}
    for rows in records.values():
        if set(rows) - ids or any(r.get("status") not in {"success", "uncertain", "failed"} for r in rows.values()):
            raise ValueError("来源结果包含未知题号或状态")
    merged, conflicts, invalid, resolutions = {}, {}, [], {}
    for q in questions:
        options = []
        sources = [rid for rid in run_ids if q.id in records[rid]]
        for rid in sources:
            row = records[rid][q.id]
            if row.get("answer") is None or row["status"] == "failed":
                continue
            try:
                value = format_answer(q, row["answer"], policy["answer"])
                # 结构/JSON忽略键顺序；数值显示差异保留，避免静默改掉小数尾零。
                if q.question_type == "structure":
                    obj = json.loads(value)
                    obj["cells"].sort(key=lambda c: (c["row"], c["col"]))
                    value = json.dumps(obj, ensure_ascii=False, sort_keys=True)
                if not value or len(value) > 32767:
                    raise ValueError("空答案或超过Excel字符上限")
            except (PipelineError, ValueError, TypeError, KeyError) as exc:
                invalid.append({"id": q.id, "run_id": rid, "error": str(exc)})
                continue
            options.append((rid, row, value))
        distinct = {v for _, _, v in options}
        chosen = None
        if len(distinct) > 1:
            variants = [{"run_id": rid, "status": row["status"], "answer": row["answer"]}
                        for rid, row, _ in options]
            chosen = next((o for o in options if o[0] == prefer_run), None)
            if chosen:
                resolutions[q.id] = {"preferred_run": prefer_run, "variants": variants}
            else:
                conflicts[q.id] = variants
        elif options:
            # 同答案优先成功/已复核，平局按显式输入顺序，不按目录时间猜优劣。
            chosen = max(options, key=lambda o: (o[1]["status"] == "success", o[1].get("validation_level") == "reviewed"))
        if chosen:
            rid, row, _ = chosen
            merged[q.id] = {**copy.deepcopy(row), "inherited_from": rid,
                            "source_run_id": row.get("source_run_id") or row.get("inherited_from") or rid,
                            "merge_sources": sources}
        elif sources:
            merged[q.id] = {"id": q.id, "status": "failed", "answer": None, "evidence": [],
                            "warnings": ["合并答案冲突，需显式选择来源" if q.id in conflicts else "来源无合法候选"],
                            "merge_sources": sources, "route": "merge"}
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = Path(cfg["paths"]["outputs"]) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    with run_lock(folder):
        snap = copy.deepcopy(reference)
        snap.update(run_id=run_id, kind="merge", base_run=None, source_runs=run_ids,
                    selected_ids=list(merged), merge_prefer_run=prefer_run)
        atomic_json(folder / "snapshot.json", snap)
        save_results(folder, merged)
        exported = export_submission(questions, merged, cfg["paths"]["template"], folder / "submission.xlsx",
                                     policy["submit_uncertain"], policy["answer"], policy["submit_unlocalized"],
                                     reference["config"]["run"].get("fill_missing_answers", False))
        summary = {"run_id": run_id, "kind": "merge", "ok": not conflicts and not invalid,
                   "source_runs": run_ids, "model_attempts": 0, "usage": {"total_tokens": 0},
                   "states": dict(Counter(r["status"] for r in merged.values())),
                   "conflict_ids": list(conflicts), "conflicts": conflicts, "resolved_conflicts": resolutions,
                   "invalid_answers": invalid, **exported}
        atomic_json(folder / "report.json", summary)
        return summary
