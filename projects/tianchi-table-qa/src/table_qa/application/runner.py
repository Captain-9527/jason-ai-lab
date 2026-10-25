"""运行生命周期：输入快照、按文档调度、原子结果、续跑与提交复检。"""
import fcntl
import json
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
from copy import deepcopy
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import ROOT
from ..infrastructure.excel import export_submission, read_questions
from ..infrastructure.model_client import ModelClient
from ..infrastructure.storage import atomic_json, digest, file_hash, read_json, read_results, save_results
from ..infrastructure.tracing import Trace, redact
from ..infrastructure.metrics import efficiency_stats
from ..processing.document import prepare_document
from ..processing.views import enrich_pages
from ..validation.consistency import check_document_consistency
from ..processing.table import recognize_tables
from ..schemas import AnswerResult, BudgetExceeded, PipelineError, RunAborted
from ..validation.input import check_inputs
from ..validation.answer import ANSWER_CONTRACT_VERSION, is_completed, validate_candidate_answer, format_answer
from ..validation.table import bbox, validate_table
from ..validation.structure_scope import has_full_structure_check
from .pipeline import answer_question
from .routing import classify


def inspect_inputs(cfg):
    questions = read_questions(cfg["paths"]["questions"])
    normalized, report = check_inputs(questions, cfg)
    report["types"] = dict(Counter(q.question_type for q in normalized))
    report["routes"] = dict(Counter(classify(q, cfg)["category"] for q in normalized))
    report["document_count"] = len({q.file_name for q in normalized})
    return normalized, report


def snapshot(cfg, questions):
    files = {q.file_name for q in questions}
    dataset = {"questions": [{k: v for k, v in asdict(q).items() if k != "route"} for q in questions],
               "files": {n: file_hash(Path(cfg["paths"]["files"]) / n) for n in sorted(files)}}
    code = {str(p.relative_to(ROOT)): file_hash(p) for p in sorted((ROOT / "src").rglob("*.py"))}
    effective = {k: v for k, v in cfg.items() if k != "paths"}
    runtime = {p: version(p) for p in ("openai", "openpyxl", "PyYAML", "Pillow", "PyMuPDF", "opencv-python-headless", "numpy")}
    return {"schema_version": 1, "config": redact(cfg), "dataset": dataset,
            "dataset_hash": digest(dataset), "code": code,
            "runtime": runtime,
            "execution_hash": digest({"dataset": dataset, "config": effective, "code": code, "runtime": runtime}),
            "template_hash": file_hash(cfg["paths"]["template"])}


def run_path(cfg, run_id):
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("运行编号必须是目录名，不能包含路径")
    root = Path(cfg["paths"]["outputs"]).resolve()
    path = (root / run_id).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise ValueError("指定运行不存在或路径非法")
    return path


@contextmanager
def run_lock(folder):
    with (Path(folder) / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("该运行正在执行，不能同时续跑或导出") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def trace_stats(folder):
    counts = Counter()
    tokens = Counter()
    routes, seen = {}, {}
    events = []
    path = Path(folder) / "trace.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            events.append(e)
            counts[e["event"]] += 1
            category = e.get("route_category", "shared_document")
            bucket = routes.setdefault(category, {"model_attempts": 0, "cache_hits": 0, "coordinate_warnings": 0, "review_attempts": 0, "repair_attempts": 0, "total_tokens": 0})
            seen.setdefault(category, set())
            if e.get("question_id"):
                seen[category].add(e["question_id"])
            if e["event"] == "model_attempt":
                bucket["model_attempts"] += 1
                if e.get("stage") in {"review", "repair"}:
                    bucket[e["stage"] + "_attempts"] += 1
            if e["event"] == "cache_hit":
                bucket["cache_hits"] += 1
            if e["event"] == "coordinate_warning":
                bucket["coordinate_warnings"] += 1
            if e["event"] == "model_response":
                bucket["total_tokens"] += (e.get("usage") or {}).get("total_tokens", 0) or 0
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    tokens[k] += (e.get("usage") or {}).get(k, 0) or 0
    for category, bucket in routes.items():
        bucket["question_count"] = len(seen[category])
        bucket["attempts_per_question"] = bucket["model_attempts"] / len(seen[category]) if seen[category] else None
    return {"model_attempts": counts["model_attempt"], "cache_hits": counts["cache_hit"], "usage": dict(tokens),
            "coordinate_warning_count": counts["coordinate_warning"], "route_usage": routes,
            "recognition_reused_documents": counts["recognition_reused"],
            **efficiency_stats(events, read_results(folder))}


def changed_input_documents(previous, current):
    old, new = previous["dataset"], current["dataset"]
    if old["questions"] != new["questions"] or set(old["files"]) != set(new["files"]):
        raise ValueError("题目清单或关联文件集合已变化，不能仅迁移文档，请新建运行")
    return {name for name, value in new["files"].items() if value != old["files"][name]}


def export_run(cfg, run_id, fill_missing=None, answer_profile=None):
    folder = run_path(cfg, run_id)
    with run_lock(folder):
        previous = read_json(folder / "snapshot.json")
        if file_hash(cfg["paths"]["template"]) != previous["template_hash"]:
            raise ValueError("提交模板已变化，不能无提示复用旧运行")
        from ..schemas import Question
        questions = [Question(**q) for q in previous["dataset"]["questions"]]
        saved = previous["config"]
        profiles = {'numbers': {'array_numbers': 'number'}, 'strings': {'array_numbers': 'string'},
                    'language_zh': {'language_names': 'zh'}, 'width': {'normalize_text_width': True}}
        if answer_profile is not None and answer_profile not in profiles:
            raise ValueError('未知答案导出实验配置')
        answer_policy = {**saved.get('answer', {'preserve_decimal_zeros': False}), **profiles.get(answer_profile, {})}
        filename = f'submission-{answer_profile}.xlsx' if answer_profile else 'submission.xlsx'
        fill = saved["run"].get("fill_missing_answers", False) if fill_missing is None else fill_missing
        baseline = {}
        if fill and previous.get("base_run"):
            base_folder = run_path(cfg, previous["base_run"])
            base_snapshot = read_json(base_folder / "snapshot.json")
            excluded = set()
            if base_snapshot["dataset_hash"] != previous["dataset_hash"]:
                changed = changed_input_documents(base_snapshot, previous)
                migration = previous.get("input_migration", {})
                if (migration.get("source_run") != previous["base_run"]
                        or migration.get("source_dataset_hash") != base_snapshot["dataset_hash"]
                        or set(migration.get("changed_documents", [])) != changed):
                    raise ValueError("导出基线题目集不一致且没有有效的文档迁移记录")
                excluded = {q["id"] for q in previous["dataset"]["questions"] if q["file_name"] in changed}
            baseline = {i: r for i, r in read_results(base_folder).items() if i not in excluded}
        report = export_submission(questions, read_results(folder), cfg["paths"]["template"], folder / filename,
                                   saved["run"].get("submit_uncertain", False), answer_policy,
                                   saved["run"].get("submit_unlocalized", False), fill, baseline)
        report['answer_profile'] = answer_profile
        report['submission_file'] = filename
        atomic_json(folder / (f'export_report-{answer_profile}.json' if answer_profile else 'export_report.json'), report)
        return {"run_id": run_id, **report}


def retain_baseline_candidate(q, record, previous, source_run, policy, retain_unverified_change=False):
    """本轮无合法答案时保留旧候选；旧坐标不能用于已旋转的新页面。"""
    changed_unverified = False
    try:
        validate_candidate_answer(q, record.get("answer"), policy)
        if (retain_unverified_change and record.get('status') == 'uncertain'
                and previous.get('status') == 'success'):
            validate_candidate_answer(q, previous.get('answer'), policy)
            changed_unverified = (format_answer(q, record['answer'], policy) !=
                                  format_answer(q, previous['answer'], policy))
        if not changed_unverified:
            return record
        if (q.question_type == 'structure' and has_full_structure_check(q, record)
                and not has_full_structure_check(q, previous)):
            # 新候选仍保留uncertain；仅取消无整表依据的旧success的优先权。
            result = deepcopy(record)
            result['baseline_not_preferred'] = 'missing_or_invalid_full_table_check'
            result['warnings'] = list(result.get('warnings', [])) + [
                '旧成功答案未通过当前整表范围协议，不以旧success覆盖本轮已有完整计数依据的候选；仍保留本轮风险状态']
            return result
    except (ValueError, TypeError, KeyError):
        pass
    if previous.get("status") not in {"success", "uncertain"}:
        return record
    try:
        validate_candidate_answer(q, previous.get("answer"), policy)
    except (ValueError, TypeError, KeyError):
        return record
    result = deepcopy(record)
    result['rejected_new_candidate'] = deepcopy(record.get('selected_candidate'))
    result['rejected_new_facts'] = deepcopy(record.get('facts', []))
    result['facts'] = []  # 拒绝新候选后，不能把其操作数当作已采用旧答案的事实。
    result['baseline_fallback_reason'] = 'unverified_answer_change' if changed_unverified else 'no_legal_answer'
    candidate = {"answer": deepcopy(previous["answer"]), "evidence": [],
                 "evidence_status": "unlocalized", "source_run_id": source_run}
    result.update(status="uncertain", answer=candidate["answer"], evidence=[],
                  answer_contract_version=previous.get("answer_contract_version"),
                  selected_candidate=candidate, selection_reason="baseline_fallback", validation_level="uncertain",
                  baseline_candidate={"source_run_id": source_run, "status": previous["status"],
                                      "answer": deepcopy(previous["answer"]),
                                      "evidence": deepcopy(previous.get("evidence", []))})
    reason = '本轮答案有未解决风险且改变了旧成功答案' if changed_unverified else '本轮无合法答案'
    result["warnings"] = list(result.get("warnings", [])) + [reason + "，保留基线候选；未经本轮验证，旧坐标仅供来源追溯"]
    return result


def reuse_document_tables(cfg, base, file_name, pages, trace):
    recognition = {"locate", "blocks", "recognize", "boundary"}
    if (not base or not cfg["run"].get("reuse_recognition", False)
            or cfg["run"].get("refresh_model_cache", False)
            or cfg["run"].get("recognition_block_height") is not None
            or recognition.intersection(cfg["run"].get("refresh_stages", []))):
        return None
    pending, seen = [base], set()
    source_hash = file_hash(Path(cfg["paths"]["files"]) / file_name)
    page_hashes = [(p["page"], p["width"], p["height"], file_hash(p["image"])) for p in pages]
    while pending:
        run_id = pending.pop(0)
        if run_id in seen:
            continue
        seen.add(run_id)
        folder = run_path(cfg, run_id)
        saved = read_json(folder / "snapshot.json")
        if saved.get("base_run"):
            pending.append(saved["base_run"])
        pending.extend(saved.get("source_runs", []))
        path = folder / "artifacts" / digest(file_name)[:16] / "tables/structures.json"
        if not path.is_file():
            continue
        try:
            if saved["dataset"]["files"].get(file_name) != source_hash:
                raise ValueError("源文档指纹不一致")
            key = digest({"source": source_hash, "options": saved["config"]["processing"],
                          "code": saved["code"]["src/table_qa/processing/document.py"]})
            old_pages = read_json(Path(cfg["paths"]["cache"]) / "documents" / key / "pages.json")
            hashes = [(p["page"], p["width"], p["height"], file_hash(p["image"])) for p in old_pages]
            if hashes != page_hashes:
                raise ValueError("处理后页面指纹不一致")
            tables = read_json(path)
            if not isinstance(tables, list) or not tables or any(t.get("incomplete") for t in tables):
                raise ValueError("没有完整的识别产物")
            lookup = {p["page"]: p for p in pages}
            table_ids, cell_ids = set(), set()
            for table in tables:
                validate_table(table)
                if not isinstance(table.get("table_id"), str) or table["table_id"] in table_ids:
                    raise ValueError("表格ID缺失或重复")
                table_ids.add(table["table_id"])
                for cell in table["cells"]:
                    if not isinstance(cell.get("id"), str) or cell["id"] in cell_ids or not cell.get("sources"):
                        raise ValueError("单元格ID或来源无效")
                    cell_ids.add(cell["id"])
                    for source in cell["sources"]:
                        page = lookup[source["page"]]
                        bbox(source["bbox"], page["width"], page["height"])
            for table in tables:
                table.setdefault("recognition_source", {"run_id": run_id, "model": saved["config"]["model"]["vision"]})
            trace.emit("recognition_reused", source_run=run_id,
                       recognition_sources=[t["recognition_source"] for t in tables],
                       table_count=len(tables), page_hashes=page_hashes)
            return tables
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            trace.emit("recognition_reuse_rejected", source_run=run_id, reason=str(exc))
    return None


def execute(cfg, mode="run", ids=None, base=None, model_factory=ModelClient, progress=None):
    if not cfg["run"]["allow_local_processing"]:
        raise ValueError("本地预处理许可尚未确认；确认后使用 --allow-local-processing 或更新配置")
    questions, report = inspect_inputs(cfg)
    if not report["ok"]:
        raise ValueError("输入校验失败，请先执行 check；原始异常不会静默修正")
    current = snapshot(cfg, questions)
    all_ids = {q.id for q in questions}
    selected = set(ids) if ids is not None else all_ids
    if not selected or selected - all_ids:
        raise ValueError("指定题目 ID 为空或不在题目清单中")
    results, baseline_results = {}, {}
    changed_documents, invalidated_ids, migration = set(), set(), {}
    if mode in {"resume", "rerun"}:
        origin = run_path(cfg, base)
        previous = read_json(origin / "snapshot.json")
        if mode == "resume" and previous.get("kind") == "merge":
            raise ValueError("合并运行不能 resume；请 rerun --base 合并编号 --failed 补齐题目")
        if previous["dataset_hash"] != current["dataset_hash"]:
            if mode != "rerun" or not cfg["run"].get("accept_document_changes", False):
                raise ValueError("题目或源文件已变化，禁止继承旧结果；仅文档变更可显式使用 --accept-document-changes")
            changed_documents = changed_input_documents(previous, current)
            invalidated_ids = {q.id for q in questions if q.file_name in changed_documents}
            if not invalidated_ids <= selected:
                missing = ",".join(sorted(invalidated_ids - selected))
                raise ValueError(f"文档迁移必须选中变更文件的全部关联题，尚未选中：{missing}")
            migration = {"source_run": base, "source_dataset_hash": previous["dataset_hash"],
                         "changed_documents": sorted(changed_documents), "invalidated_ids": sorted(invalidated_ids)}
        if mode == "resume" and previous["execution_hash"] != current["execution_hash"]:
            raise ValueError("代码、配置或提示词已变化，请使用 rerun 创建新版本")
        if mode == "rerun" and ids is None:
            raise ValueError("rerun 必须显式指定 --ids，或使用 --failed")
    if mode == "resume":
        folder = origin
    else:
        run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        folder = Path(cfg["paths"]["outputs"]) / run_id
        folder.mkdir(parents=True, exist_ok=False)
    with run_lock(folder):
        if mode == "resume":
            # 锁内重新读取，避免同时续跑导致状态覆盖。
            results = read_results(folder)
            baseline_results = deepcopy(results)
            if ids is not None and selected - set(previous["selected_ids"]):
                raise ValueError("resume 不能扩展原运行题目范围，请使用 rerun")
            selected &= set(previous["selected_ids"])
            selected -= {q.id for q in questions if is_completed(q, results.get(q.id, {}), cfg.get("answer"))}
        elif mode == "rerun":
            with run_lock(origin):
                baseline_results = {i: r for i, r in read_results(origin).items() if i not in invalidated_ids}
                results = {i: {**r, "inherited_from": base, "inherited_validation": "not_rechecked"}
                           for i, r in baseline_results.items() if i not in selected}
        if mode != "resume":
            current.update({"run_id": folder.name, "base_run": base, "selected_ids": sorted(selected), "input_report": report,
                            "input_migration": migration})
            atomic_json(folder / "snapshot.json", current)
        trace = Trace(folder / "trace.jsonl", cfg["run"]["trace_payloads"])
        stats = trace_stats(folder)
        # 检查凭据发生在云端请求之前；不打印密钥，不自动读取课程密钥。
        model = model_factory(cfg, trace, folder, stats["model_attempts"]) if selected else None
        trace.emit("run_start", mode=mode, selected_count=len(selected), run_id=folder.name)
        lock, stop = threading.Lock(), threading.Event()
        start = time.monotonic()
        question_map = {q.id: q for q in questions}
        completed = set()
        if progress:
            progress(0, len(selected), {"run_id": folder.name})

        def store(record):
            record = retain_baseline_candidate(question_map[record["id"]], record,
                                               baseline_results.get(record["id"], {}), base, cfg.get("answer"),
                                               cfg['run'].get('retain_baseline_on_unverified_change', False))
            with lock:
                results[record["id"]] = redact(record)
                save_results(folder, results)
                completed.add(record['id'])
                if progress:
                    progress(len(completed), len(selected), record)

        def group(file_name, group_questions):
            if stop.is_set():
                return
            with trace.scope(document=file_name):
                if hasattr(model, "set_document_refresh"):
                    model.set_document_refresh(file_name in changed_documents)
                try:
                    with trace.stage("prepare_document") as out:
                        pages = prepare_document(Path(cfg["paths"]["files"]) / file_name, cfg, trace)
                        out["page_count"] = len(pages)
                    model.reset_question_budget()
                    tables, table_error = None, None
                    work = folder / "artifacts" / digest(file_name)[:16]
                    if cfg['run'].get('single_pass', False):
                        pages = enrich_pages(Path(cfg['paths']['files']) / file_name, pages, cfg, work, trace)
                    if any(classify(q, cfg)["needs_tables"] for q in group_questions):
                        try:
                            with trace.stage("recognize_document") as out:
                                tables = reuse_document_tables(cfg, base, file_name, pages, trace)
                                if tables is None:
                                    tables = recognize_tables(pages, model, cfg, work)
                                else:
                                    atomic_json(work / "tables/structures.json", tables)
                                out["tables"] = tables
                        except PipelineError as exc:
                            table_error = str(exc)
                            trace.emit("recognition_failed", error=table_error)
                    for q in group_questions:
                        if stop.is_set():
                            break
                        model.reset_question_budget()
                        with trace.scope(question_id=q.id, route_category=classify(q, cfg)["category"]):
                            try:
                                record = answer_question(q, pages, tables, model, cfg, trace, work,
                                                         fresh=(mode == "rerun" and not cfg["run"].get("reuse_answer_cache", False)),
                                                         table_error=table_error)
                            except (BudgetExceeded, RunAborted):
                                raise
                            except Exception as exc:
                                trace.emit("question_failed", error_type=type(exc).__name__, error=str(exc)[:1000])
                                record = asdict(AnswerResult(q.id, "failed", warnings=[str(exc)[:500]]))
                            store(record)
                except (BudgetExceeded, RunAborted):
                    stop.set()
                    raise
                except Exception as exc:
                    trace.emit("document_failed", error_type=type(exc).__name__, error=str(exc)[:1000])
                    for q in group_questions:
                        if q.id not in results:
                            store(asdict(AnswerResult(q.id, "failed", warnings=[str(exc)[:500]])))

        groups = defaultdict(list)
        for q in questions:
            if q.id in selected:
                groups[q.file_name].append(q)
        failure = None
        try:
            with ThreadPoolExecutor(max_workers=cfg["run"]["workers"]) as pool:
                futures = [pool.submit(group, name, qs) for name, qs in groups.items()]
                try:
                    for future in as_completed(futures):
                        future.result()
                except BaseException:
                    stop.set()
                    if model is not None and hasattr(model, "cancel"):
                        model.cancel()
                    for future in futures:
                        future.cancel()
                    raise
        except BaseException as exc:
            failure = exc
            trace.emit("run_interrupted", error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            # 中断前尚未产出记录的选中题，也不能无提示丢弃基线的合法候选。
            for i in selected - results.keys():
                old = baseline_results.get(i, {})
                pending = asdict(AnswerResult(i, "failed", warnings=["本轮中断，尚未完成答题"]))
                retained = retain_baseline_candidate(question_map[i], pending, old, base, cfg.get("answer"))
                if retained.get("selection_reason") == "baseline_fallback":
                    results[i] = redact(retained)
            save_results(folder, results)
            consistency = check_document_consistency(questions, results) if cfg['run'].get('single_pass', False) else []
            if consistency:
                trace.emit('document_consistency_conflict', conflicts=consistency)
                if cfg['run'].get('retain_baseline_on_unverified_change', False):
                    affected = {i for item in consistency for i in item['ids']} & selected
                    for i in affected:
                        results[i] = redact(retain_baseline_candidate(question_map[i], results[i],
                            baseline_results.get(i, {}), base, cfg.get('answer'), True))
                save_results(folder, results)
            exported = export_submission(questions, results, cfg["paths"]["template"], folder / "submission.xlsx",
                                         cfg["run"]["submit_uncertain"], cfg.get("answer"), cfg["run"]["submit_unlocalized"],
                                         cfg["run"].get("fill_missing_answers", False), baseline_results)
            summary = {"run_id": folder.name, "states": dict(Counter(r["status"] for r in results.values())),
                       "duration_seconds": round(time.monotonic() - start, 3), **trace_stats(folder), **exported}
            summary['selected_count'] = len(selected)
            summary['local_states'] = dict(Counter(results[i]['status'] for i in selected if i in results))
            summary["input_migration"] = current.get("input_migration", migration) if mode != "resume" else previous.get("input_migration", {})
            summary["answer_contract_version"] = ANSWER_CONTRACT_VERSION
            summary['execution_mode'] = 'single_pass' if cfg['run'].get('single_pass', False) else 'legacy_review'
            summary['consistency_conflicts'] = consistency
            summary['consistency_checked_count'] = sum(bool(r.get('facts')) for r in results.values())
            summary["inherited_not_rechecked_count"] = sum(i not in selected for i in results)
            summary["legacy_contract_count"] = sum(r.get("answer_contract_version") != ANSWER_CONTRACT_VERSION
                                                    for r in results.values())
            summary["baseline_fallback_ids"] = sorted(i for i, r in results.items()
                                                      if i in selected and r.get("selection_reason") == "baseline_fallback")
            atomic_json(folder / "report.json", summary)
            trace.emit("run_end", report=summary)
        if failure is not None:
            raise RuntimeError(f"运行中断，已保存 {folder.name}；{type(failure).__name__}: {failure}") from None
        return summary


def compare_runs(cfg, left, right):
    a, b = run_path(cfg, left), run_path(cfg, right)
    if read_json(a / "snapshot.json")["dataset_hash"] != read_json(b / "snapshot.json")["dataset_hash"]:
        raise ValueError("不能比较不同题目或文档集的运行")
    ar, br = read_results(a), read_results(b)
    changed = [i for i in sorted(set(ar) | set(br)) if
               (ar.get(i, {}).get("status"), ar.get(i, {}).get("answer")) !=
               (br.get(i, {}).get("status"), br.get(i, {}).get("answer"))]
    answer_changed = [i for i in sorted(set(ar) | set(br))
                      if ar.get(i, {}).get("answer") != br.get(i, {}).get("answer")]
    return {"left": left, "right": right, "changed_ids": changed, "changed_count": len(changed),
            "answer_changed_ids": answer_changed, "answer_changed_count": len(answer_changed),
            "left_usage": trace_stats(a), "right_usage": trace_stats(b),
            "note": "无标准答案，只比较变化与运行用量，不代表准确率改善"}
