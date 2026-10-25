"""识别失败或复核不通过时按有限预算修复，不把不确定答案标成成功。"""
from dataclasses import asdict, replace
from pathlib import Path

from ..infrastructure.storage import digest
from ..schemas import AnswerResult, PipelineError
from ..processing.document import crop_image
from ..solving.direct import solve_direct, validate_evidence
from ..solving.structured import solve_structured
from ..validation.answer import ANSWER_CONTRACT_VERSION, answer_contract, canonical, comparison_details
from ..validation.coordinates import emit_warnings
from ..validation.review import classify_risks
from .routing import classify
from ..processing.views import question_views
from ..validation.grounding import local_checks


def answer_single(q, pages, tables, model, cfg, trace, work, fresh=False, table_error=None):
    """一条路线、零模型复核；只对协议失败做有限无历史答案修复。"""
    routing = classify(q, cfg)
    q = replace(q, route=routing['category'])
    strategy = routing['strategy']
    views = question_views(q, pages, cfg, work, trace) if strategy == 'direct' else pages
    candidate, problems, candidates = None, [], {}
    with trace.stage('solve_question', q.public()):
        for attempt in range(cfg['run']['max_repairs'] + 1):
            try:
                if attempt == 0 and strategy == 'structured':
                    if not tables:
                        raise PipelineError(table_error or '没有可用表格')
                    candidate = solve_structured(q, tables, model, fresh)
                else:
                    if attempt and strategy == 'structured':
                        views = question_views(q, pages, cfg, work, trace)
                    candidate = solve_direct(q, views, model, fresh if attempt == 0 else True,
                                             repair={'issues': [p[:300] for p in problems[-2:]], 'requires_calculation': q.route in {'calculation', 'filter'}} if attempt else None,
                                             cfg=cfg)
                candidates[strategy if attempt == 0 else f'repair_{attempt}'] = candidate
                break
            except (PipelineError, KeyError, TypeError) as exc:
                problems.append(f'协议检查: {exc}')
        issues, facts = local_checks(q, candidate, pages, cfg) if candidate else (['没有合法候选'], [])
        warnings = list(dict.fromkeys(problems + (candidate or {}).get('warnings', []) + issues))
        status = 'success' if candidate and not issues and not candidate.get('warnings') else 'uncertain'
        result = asdict(AnswerResult(q.id, status, (candidate or {}).get('answer'),
                                    (candidate or {}).get('evidence', []), warnings, strategy))
        result.update(answer_contract_version=ANSWER_CONTRACT_VERSION, execution_mode='single_pass',
                      candidates=candidates, selected_candidate=candidate, routing=routing,
                      selection_reason='single_route' if candidate else 'no_candidate',
                      validation_level='program_checked' if status == 'success' else 'uncertain',
                      program_checks={'issues': issues, 'model_reviewed': False}, facts=facts,
                      review={}, review_history=[])
        trace.emit('program_validation', status=status, issues=issues)
        return result


def review(q, candidates, pages, model, cfg, extra=None, fresh=False):
    differences = comparison_details(q, candidates["direct"]["answer"], candidates["structured"]["answer"]) if {"direct", "structured"} <= candidates.keys() else {}
    if differences.get("dimensions_equal") is False:
        # 整表尺寸不一致不是等价表示；跳过无效择优，要求修复阶段重新核对整表。
        risk = {"severity": "blocking", "code": "structure_dimensions_conflict",
                "message": "候选整表行列数冲突，须重新核对目标整表，不能按表头范围解释尺寸"}
        return False, {"verdict": "retry", "confidence": 0, "preferred": "none", "risks": [risk],
                       "blocking_risks": [risk], "advisory_risks": [], "accepted": False,
                       "comparison": differences, "program_check": True}
    result = model.ask("review", {"question": {**q.public(), "answer_contract": answer_contract(q)}, "candidates": candidates,
                       "comparison": differences,
                       "pages": [{k: p[k] for k in ("page", "width", "height")} for p in pages]},
                       [p["image"] for p in pages] + (extra or []), fresh=fresh)
    score = result.get("confidence")
    if type(score) not in (float, int) or not 0 <= score <= 1 or result.get("verdict") not in {"pass", "retry", "unknown"}:
        raise PipelineError("复核协议不合法")
    blocking, advisory = classify_risks(result.get("risks"), cfg["run"].get("review_risk_policy", "graded"))
    # 低置信仍需修复；中高置信也必须通过证据复核且无未解决风险。
    preferred = result.get("preferred", "none")
    if preferred != "none" and preferred not in candidates:
        raise PipelineError("复核 preferred 未指向已有候选")
    if q.question_type == "structure":
        chosen = candidates.get(preferred) or next(iter(candidates.values()))
        check = result.get("full_table_check", {})
        answer = chosen["answer"]
        valid = (isinstance(check, dict) and check.get("scope") == "full_table"
                 and all(type(check.get(k)) is int and check[k] == answer[k] for k in ("row_count", "col_count"))
                 and type(check.get("page")) is int and check["page"] in {p["page"] for p in pages}
                 and isinstance(check.get("basis"), str) and bool(check["basis"].strip()))
        if not valid:
            blocking.append({"severity": "blocking", "code": "full_table_dimensions_unverified",
                             "message": "缺少与所选答案一致的整表尺寸核验（不能仅计表头行数）"})
    passed = result["verdict"] == "pass" and score >= cfg["run"]["min_review_confidence"] and not blocking
    result = {**result, "blocking_risks": blocking, "advisory_risks": advisory, "accepted": passed}
    return passed, result


def answer_question(q, pages, tables, model, cfg, trace, work, fresh=False, table_error=None):
    if cfg['run'].get('single_pass', False):
        return answer_single(q, pages, tables, model, cfg, trace, work, fresh, table_error)
    routing = classify(q, cfg)
    q = replace(q, route=routing["category"])
    strategy = routing["strategy"]
    candidates, problems = {}, []
    review_history = []

    def finish(status, candidate, route, assessment, reason):
        if candidate and not candidate.get("evidence"):
            status = "uncertain"
            outcome["status"] = status
        notes = ["复核提示: " + r["message"] for r in assessment.get("advisory_risks", [])]
        all_warnings = list(dict.fromkeys(problems + (candidate or {}).get("warnings", []) + notes))
        result = asdict(AnswerResult(q.id, status, (candidate or {}).get("answer"),
                                    (candidate or {}).get("evidence", []), all_warnings, route))
        result.update(answer_contract_version=ANSWER_CONTRACT_VERSION,
                      candidates=candidates, review=assessment, review_history=review_history, routing=routing,
                      selected_candidate=candidate, selection_reason=reason,
                      validation_level="reviewed" if status == "success" and assessment else
                      "format_evidence" if status == "success" else "uncertain")
        return result
    with trace.stage("solve_question", q.public()) as outcome:
        for name in (["direct", "structured"] if strategy == "compare" else [strategy]):
            try:
                if name == "structured":
                    if not tables:
                        raise PipelineError(table_error or "没有可用表格")
                    candidates[name] = solve_structured(q, tables, model, fresh)
                else:
                    candidates[name] = solve_direct(q, pages, model, fresh, cfg=cfg)
                problems.extend(candidates[name].get("warnings", []))
            except (PipelineError, KeyError, TypeError) as exc:
                problems.append(f"{name}: {exc}")
        mismatch = len(candidates) == 2 and canonical(q, candidates["direct"]["answer"]) != canonical(q, candidates["structured"]["answer"])
        candidate = candidates.get("direct") or candidates.get("structured")
        assessment = {}
        risky = q.route in {"calculation", "filter", "structure", "other"} or len(pages) > 1 or bool(problems) or mismatch
        derived = any(c.get("operation") == "calculate" for c in candidates.values())
        needs_review = q.question_type == "structure" or mismatch or derived or any(c.get("requires_review") for c in candidates.values()) or (cfg["run"]["review"] and
                       (cfg["run"].get("review_policy", "always") == "always" or risky))
        if candidate and not mismatch:
            try:
                passed, assessment = review(q, candidates, pages, model, cfg, fresh=fresh) if needs_review else (True, {})
                if needs_review:
                    review_history.append(assessment)
                if passed and assessment.get("preferred") in candidates:
                    candidate = candidates[assessment["preferred"]]
                candidate["review_verdict"] = assessment.get("verdict", "not_reviewed")
                if passed:
                    outcome["status"] = "success"
                    return finish("success", candidate, strategy, assessment, "review_passed" if needs_review else "low_risk")
            except (PipelineError, KeyError, TypeError) as exc:
                problems.append(f"review: {exc}")
        elif mismatch:
            problems.append("直接路线与结构化路线答案不一致")
            try:
                passed, assessment = review(q, candidates, pages, model, cfg, fresh=fresh)
                review_history.append(assessment)
                preferred = assessment.get("preferred", "none")
                if passed and preferred in candidates:
                    candidate = candidates[preferred]
                    candidate["review_verdict"] = "pass"
                    outcome["status"] = "success"
                    return finish("success", candidate, strategy, assessment, "review_preferred")
            except (PipelineError, KeyError, TypeError) as exc:
                problems.append(f"review: {exc}")
        for attempt in range(cfg["run"]["max_repairs"]):
            regions = assessment.get("regions") or (candidate or {}).get("evidence") or []
            extra = []
            if regions:
                try:
                    # review 输出仍在模型坐标制；候选证据已转换，不能重复转换。
                    valid = validate_evidence(regions, pages, cfg if assessment.get("regions") else None)
                    for i, r in enumerate(valid[:4]):
                        page = next(p for p in pages if p["page"] == r["page"])
                        target = Path(work) / "repairs" / f"{digest(q.id)[:12]}-{attempt}-{i}.png"
                        margin = cfg["processing"]["crop_margin"]
                        l, t, rr, b = r["bbox"]
                        area = [max(0, l - margin), max(0, t - margin),
                                min(page["width"], rr + margin), min(page["height"], b + margin)]
                        extra.append(crop_image(page["image"], area, target,
                                     cfg["processing"]["local_zoom"], cfg["processing"]["max_pixels"]))
                except PipelineError as exc:
                    problems.append(f"局部区域无效，回看全页: {exc}")
                    emit_warnings(model, "review", [str(exc)])
            try:
                after_retry = assessment.get("verdict") == "retry"
                candidate = solve_direct(q, pages, model, fresh=True,
                                         repair={"candidates": candidates, "issues": problems, "review": assessment,
                                                 "requires_calculation": derived},
                                         extra_images=extra, cfg=cfg)
                candidate["repair_after_retry"] = after_retry
                problems.extend(candidate.get("warnings", []))
                candidates[f"repair_{attempt + 1}"] = candidate
                # 修复后的结果总是回看原图复核，不能因关闭普通复核而直接放行。
                passed, assessment = review(q, {"repaired": candidate}, pages, model, cfg, extra, fresh=True)
                review_history.append(assessment)
                candidate["review_verdict"] = assessment.get("verdict")
                if passed:
                    outcome["status"] = "success"
                    return finish("success", candidate, "repair", assessment, "review_passed")
            except (PipelineError, KeyError, TypeError) as exc:
                problems.append(f"repair: {exc}")
        outcome["status"] = "uncertain"
        # 一致性和复算仅是候选排序依据，不将它们冒充正确率证明。
        ranked = []
        for name, item in candidates.items():
            agreed = any(name != other and canonical(q, item["answer"]) == canonical(q, value["answer"])
                         for other, value in candidates.items())
            preferred_repair = item.get("repair_after_retry") and item.get("review_verdict") != "retry"
            rank = 4 if agreed else 3 if item.get("computation_checked") else 2 if preferred_repair else 1 if name == "direct" else 0
            ranked.append((rank, name, item))
        selected = max(ranked, key=lambda item: item[0]) if ranked else None
        problems.append("证据或复核不足；保留候选但不标记为验证通过")
        return finish("uncertain", selected[2] if selected else None, strategy, assessment,
                      {4: "agreement", 3: "computed", 2: "repair_after_retry", 1: "direct", 0: "legal_candidate"}[selected[0]] if selected else "no_candidate")
