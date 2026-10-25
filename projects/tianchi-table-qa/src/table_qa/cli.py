"""面向使用者的命令入口；只在 run/resume/rerun 中初始化模型。"""
import argparse
import json
import sys
from pathlib import Path

from .application.runner import compare_runs, execute, export_run, inspect_inputs, run_path
from .config import load_config, validate_config
from .infrastructure.storage import read_results
from .infrastructure.tracing import redact
from .schemas import Question
from .validation.answer import is_completed


def print_run_summary(result, cfg):
    """控制台只展示验收所需摘要；完整信息仍由runner写入report.json。"""
    states = result.get('local_states', {})
    labels = {'success': '成功', 'uncertain': '不确定', 'failed': '失败'}
    counts = '，'.join(f'{labels.get(k, k)} {v}' for k, v in states.items()) or '无新结果'
    print(f"本轮：{result.get('selected_count', 0)} 题（{counts}）；继承 {result.get('inherited_not_rechecked_count', 0)} 题")
    print(f"耗时 {result.get('duration_seconds', 0):g} 秒 | 模型请求 {result.get('model_attempts', 0)} 次"
          f" | 缓存 {result.get('cache_hits', 0)} 次 | Token {result.get('usage', {}).get('total_tokens', 0):,}")
    alerts = []
    for key, label in [('placeholder_count', '占位答案'), ('blank_count', '空答案')]:
        if result.get(key):
            alerts.append(f'{label} {result[key]} 题')
    for key, label in [('format_failed_ids', '格式失败'), ('baseline_fallback_ids', '回退旧答案')]:
        if result.get(key):
            alerts.append(f'{label} {len(result[key])} 题')
    if alerts:
        print('注意（全量导出）：' + '；'.join(alerts) + '，详见报告')
    folder = Path(cfg['paths']['outputs']) / result['run_id']
    print(f"结果：{folder / 'submission.xlsx'}")
    print(f"报告：{folder / 'report.json'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="天池复杂表格问答：先 check，再小批量 run")
    parser.add_argument("command", choices=["check", "run", "resume", "rerun", "export", "compare", "merge"])
    parser.add_argument("--runs", help="merge 的来源运行编号，逗号分隔")
    parser.add_argument("--prefer-run", help="merge 冲突时显式优先采用此来源；未覆盖的冲突仍留空")
    parser.add_argument("--config")
    parser.add_argument('--json', action='store_true', help='输出完整JSON报告；执行进度写入标准错误')
    parser.add_argument("--ids", help="逗号分隔题目 ID；不填写表示全部")
    parser.add_argument("--run", dest="run_id", help="resume/export 的运行编号")
    parser.add_argument("--fill-missing", action="store_true", help="仅 export：显式启用非空导出，原始结果不变，不调用模型")
    parser.add_argument('--answer-profile', choices=['numbers', 'strings', 'language_zh', 'width'],
                        help='仅export：单变量答案格式实验，另存新Excel，不覆盖原提交')
    parser.add_argument("--base", help="rerun 的基线运行编号")
    parser.add_argument("--failed", action="store_true", help="重跑非成功、未处理及成功但答案空白或格式非法的题目")
    parser.add_argument("--left")
    parser.add_argument("--right")
    parser.add_argument("--strategy", choices=["auto", "direct", "structured", "compare"])
    parser.add_argument("--refresh-model-cache", action="store_true", help="强制刷新模型响应，仍复用页面缓存")
    parser.add_argument("--refresh-stages", help="逗号分隔的模型阶段，仅刷新指定阶段；recognition展开为全部识别阶段")
    parser.add_argument("--reuse-recognition", action="store_true", help="rerun/resume：校验图片后复用基线及祖先的完整表格产物")
    parser.add_argument("--reuse-answer-cache", action="store_true", help="rerun/resume：允许命中答题缓存，仍重新执行当前校验与复核")
    parser.add_argument("--question-type", choices=["structure", "extract", "thinking"], help="仅rerun：重审指定题型，包含历史success")
    parser.add_argument("--block-height", type=int, help="仅覆盖识别分块高度，不改变页面预处理缓存")
    parser.add_argument("--review-policy", choices=["always", "on_risk"], help="本批复核策略，不改变识别缓存")
    parser.add_argument("--accept-document-changes", action="store_true", help="显式迁移文档变更；必须重跑变更文件全部关联题，不继承旧图答案")
    parser.add_argument("--accept-corrections", action="store_true", help="确认使用已列明的输入修正规则")
    parser.add_argument("--allow-local-processing", action="store_true", help="确认本地处理许可或仅用于学习测试")
    args = parser.parse_args(argv)
    try:
        if args.answer_profile and args.command != 'export':
            raise ValueError('--answer-profile仅用于export')
        if args.accept_document_changes and args.command not in {"rerun", "resume"}:
            raise ValueError("--accept-document-changes 仅用于 rerun/resume；resume仍不允许改变输入")
        if (args.reuse_recognition or args.reuse_answer_cache) and args.command not in {"rerun", "resume"}:
            raise ValueError("缓存复用开关仅用于 rerun/resume")
        if args.review_policy and args.command not in {"run", "rerun", "resume"}:
            raise ValueError("--review-policy 仅用于 run/rerun/resume")
        if args.block_height is not None and args.command not in {"run", "rerun", "resume"}:
            raise ValueError("--block-height 仅用于 run/rerun/resume")
        if args.refresh_stages and args.command not in {"run", "rerun", "resume"}:
            raise ValueError("--refresh-stages 仅用于 run/rerun/resume")
        if args.question_type and (args.command != "rerun" or args.ids or args.failed):
            raise ValueError("--question-type 仅用于 rerun，且不能同时指定 --ids/--failed")
        if args.fill_missing and args.command != "export":
            raise ValueError("--fill-missing 只能用于 export")
        if (args.runs or args.prefer_run) and args.command != "merge":
            raise ValueError("--runs/--prefer-run 只能用于 merge")
        if args.command == "merge" and any((args.ids, args.run_id, args.base, args.failed,
                                            args.left, args.right, args.strategy, args.refresh_model_cache)):
            raise ValueError("merge 不接受执行/重跑参数，只使用 --runs 和可选 --prefer-run")
        cfg = load_config(args.config)
        if args.strategy:
            cfg["run"]["strategy"] = args.strategy
        if args.refresh_model_cache:
            cfg["run"]["refresh_model_cache"] = True
        if args.refresh_stages:
            stages = [s.strip() for s in args.refresh_stages.split(",") if s.strip()]
            expanded = []
            for stage in stages:
                expanded.extend(["locate", "blocks", "recognize", "boundary"] if stage == "recognition" else [stage])
            if not expanded:
                raise ValueError("--refresh-stages 不能为空")
            cfg["run"]["refresh_stages"] = sorted(set(expanded))
        if args.accept_document_changes:
            cfg["run"]["accept_document_changes"] = True
        if args.review_policy:
            cfg["run"]["review_policy"] = args.review_policy
        if args.block_height is not None:
            cfg["run"]["recognition_block_height"] = args.block_height
        if args.reuse_recognition:
            cfg["run"]["reuse_recognition"] = True
        if args.reuse_answer_cache:
            cfg["run"]["reuse_answer_cache"] = True
        if args.accept_corrections:
            cfg["run"]["corrections_enabled"] = True
        if args.allow_local_processing:
            cfg["run"]["allow_local_processing"] = True
        validate_config(cfg)
        if args.command == "check":
            _, result = inspect_inputs(cfg)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        if args.command == "merge":
            from .application.merging import merge_runs
            result = merge_runs(cfg, [r.strip() for r in (args.runs or "").split(",") if r.strip()], args.prefer_run)
        elif args.command == "export":
            result = export_run(cfg, args.run_id, True if args.fill_missing else None, args.answer_profile)
        elif args.command == "compare":
            result = compare_runs(cfg, args.left, args.right)
        else:
            ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None
            if args.question_type:
                questions, report = inspect_inputs(cfg)
                if not report["ok"]:
                    raise ValueError("输入校验失败，请先执行 check")
                ids = [q.id for q in questions if q.question_type == args.question_type]
                if not ids:
                    raise ValueError("题目集中不存在指定题型")
            if args.failed:
                if args.command != "rerun" or args.ids:
                    raise ValueError("--failed 只能用于 rerun 且不能同时指定 --ids")
                from .infrastructure.storage import read_json
                folder = run_path(cfg, args.base)
                previous = read_results(folder)
                saved = read_json(folder / "snapshot.json")
                ids = [q["id"] for q in saved["dataset"]["questions"]
                       if not is_completed(Question(**q), previous.get(q["id"], {}),
                                           saved["config"].get("answer", {"preserve_decimal_zeros": False}))]
                if not ids:
                    message = '没有需要重跑的题目，未创建运行或请求模型'
                    print(json.dumps({"ok": True, "base_run": args.base, "selected_count": 0,
                                      "message": message}, ensure_ascii=False) if args.json else message)
                    return 0
            def progress(done, total, record):
                stream = sys.stderr if args.json else sys.stdout
                if 'run_id' in record:
                    print(f"运行 {record['run_id']} | 待处理 {total} 题", file=stream, flush=True)
                else:
                    status = {'success': '成功', 'uncertain': '不确定', 'failed': '失败'}.get(record['status'], record['status'])
                    print(f"[{done}/{total}] 题目 {record['id']}：{status}", file=stream, flush=True)
            result = execute(cfg, args.command, ids, args.run_id if args.command == "resume" else args.base,
                             progress=progress)
        if args.command in {'run', 'resume', 'rerun'} and not args.json and result.get('run_id'):
            print_run_summary(result, cfg)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 2
    except Exception as exc:
        print(f"错误：{redact(str(exc))}", file=sys.stderr)
        return 2
