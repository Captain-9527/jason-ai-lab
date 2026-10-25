"""配置路径始终基于项目根目录，密钥不进入配置对象。"""
import os
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | None = None) -> dict:
    source = Path(path).resolve() if path else ROOT / "configs/settings.yaml"
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    for name, value in cfg["paths"].items():
        cfg["paths"][name] = str((ROOT / value).resolve())
    for name in ("prompts", "corrections", "routes"):
        cfg[name] = yaml.safe_load((source.parent / f"{name}.yaml").read_text(encoding="utf-8"))
    cfg["model"]["vision"] = os.getenv("QWEN_VL_MODEL") or cfg["model"]["vision"]
    cfg["model"]["text"] = os.getenv("QWEN_TEXT_MODEL") or cfg["model"]["text"] or cfg["model"]["vision"]
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    if cfg.get('answer', {}).get('numeric_locale', 'explicit') not in {'explicit', 'preserve'}:
        raise ValueError('answer.numeric_locale须为explicit/preserve')
    for name in ("local_table_orientation", "verify_pdf_evidence", "focus_table_content", "vector_grid_headers"):
        if type(cfg["processing"].get(name, False)) is not bool:
            raise ValueError(f"processing.{name} 必须为布尔值")
    if type(cfg["run"].get("single_pass", False)) is not bool:
        raise ValueError("run.single_pass 必须为布尔值")
    if type(cfg['run'].get('localize_pdf_evidence', False)) is not bool:
        raise ValueError('run.localize_pdf_evidence必须为布尔值')
    if type(cfg['run'].get('focus_ruled_pdf_tables', False)) is not bool:
        raise ValueError('run.focus_ruled_pdf_tables必须为布尔值')
    if type(cfg['run'].get('program_assembly', False)) is not bool:
        raise ValueError('run.program_assembly必须为布尔值')
    if cfg['run'].get('structure_target_policy', 'as_requested') not in {'first', 'as_requested'}:
        raise ValueError('run.structure_target_policy必须为first或as_requested')
    if cfg.get("answer", {}).get("array_nulls", "reject") not in {"reject", "empty_string"}:
        raise ValueError("answer.array_nulls 必须为 reject/empty_string")
    if cfg.get("answer", {}).get("array_numbers", "preserve") not in {"preserve", "number", "string"}:
        raise ValueError("answer.array_numbers 必须为 preserve/number/string")
    if cfg.get("answer", {}).get("language_names", "preserve") not in {"preserve", "zh"}:
        raise ValueError("answer.language_names 必须为 preserve/zh")
    if type(cfg.get("answer", {}).get("normalize_text_width", False)) is not bool:
        raise ValueError("answer.normalize_text_width 必须为布尔值")
    if type(cfg["run"].get("fill_missing_answers", False)) is not bool:
        raise ValueError("run.fill_missing_answers 必须为布尔值")
    if type(cfg['run'].get('retain_baseline_on_unverified_change', False)) is not bool:
        raise ValueError('run.retain_baseline_on_unverified_change 必须为布尔值')
    paths = {k: Path(v).resolve() for k, v in cfg["paths"].items()}
    for name in ("cache", "outputs"):
        if paths[name].is_relative_to(paths["files"]) or paths[name] in {
            paths["questions"].parent, paths["template"].parent
        }:
            raise ValueError("缓存和输出不能写入原始数据目录")
    if paths["cache"].is_relative_to(paths["outputs"]) or paths["outputs"].is_relative_to(paths["cache"]):
        raise ValueError("cache 与 outputs 必须是相互独立的目录")
    url = urlparse(cfg["model"]["base_url"])
    if url.scheme != "https" or url.hostname not in {
        "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"
    } or url.username or url.password or url.query:
        raise ValueError("base_url 必须是已允许的阿里云 HTTPS 地址，不能携带凭据")
    for value in (cfg["model"]["vision"], cfg["model"]["text"]):
        if value and not value.lower().startswith("qwen"):
            raise ValueError("比赛模式仅接受 qwen 系列模型 ID")
    for section, names in {"run": ["workers", "max_model_calls", "max_calls_per_question"],
                           "model": ["timeout", "requests_per_minute", "max_tokens"],
                           "processing": ["dpi", "max_pages", "max_pixels", "min_side", "max_scale",
                                          "block_height", "max_blocks", "max_tables_per_page", "local_zoom"]}.items():
        for name in names:
            if not isinstance(cfg[section][name], (int, float)) or cfg[section][name] <= 0:
                raise ValueError(f"{section}.{name} 必须为正数")
    if cfg["run"]["strategy"] not in {"auto", "direct", "structured", "compare"}:
        raise ValueError("未知解题策略")
    if cfg["run"]["max_repairs"] < 0 or cfg["model"]["retries"] < 0:
        raise ValueError("重试次数不能为负")
    for section, names in {"run": ["workers", "max_model_calls", "max_calls_per_question", "max_repairs"],
                           "model": ["retries", "max_tokens"],
                           "processing": ["max_pages", "max_blocks", "max_tables_per_page", "crop_margin"]}.items():
        for name in names:
            if type(cfg[section][name]) is not int or cfg[section][name] < 0:
                raise ValueError(f"{section}.{name} 必须是非负整数")
    for name in ("corrections_enabled", "allow_local_processing", "review", "trace_payloads", "refresh_model_cache", "submit_uncertain", "submit_unlocalized"):
        if type(cfg["run"][name]) is not bool:
            raise ValueError(f"run.{name} 必须为布尔值")
    if cfg["run"]["review_policy"] not in {"always", "on_risk"}:
        raise ValueError("review_policy 必须为 always 或 on_risk")
    if cfg["run"].get("review_risk_policy", "graded") not in {"graded", "strict"}:
        raise ValueError("review_risk_policy 必须为 graded 或 strict")
    if cfg["answer"]["percent"] not in {"preserve", "fraction", "number"} or cfg["answer"]["parentheses_negative"] not in {"preserve", "convert"}:
        raise ValueError("答案转换策略不合法")
    if type(cfg["answer"]["preserve_decimal_zeros"]) is not bool:
        raise ValueError("preserve_decimal_zeros 必须为布尔值")
    import re
    for rule in cfg["routes"]["rules"]:
        re.compile(rule["pattern"])
        if rule["strategy"] not in {"direct", "structured", "compare"}:
            raise ValueError("路由规则的 strategy 非法")
    if cfg["routes"]["fallback"]["strategy"] not in {"direct", "structured", "compare"}:
        raise ValueError("默认路由 strategy 非法")
    for rule in cfg["routes"]["rules"]:
        if rule.get("exclude"):
            re.compile(rule["exclude"])
    import math
    if type(cfg["processing"].get("pdf_auto_orient", False)) is not bool:
        raise ValueError("pdf_auto_orient 必须为布尔值")
    minimum = cfg["processing"].get("orientation_min_chars", 50)
    ratio = cfg["processing"].get("orientation_min_ratio", .9)
    if type(minimum) is not int or minimum < 1:
        raise ValueError("orientation_min_chars 必须为正整数")
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not .5 < ratio <= 1:
        raise ValueError("orientation_min_ratio 必须大于0.5且不超过1")
    stages = cfg["model"].get("stages", {})
    allowed_stages = {"locate", "blocks", "recognize", "boundary", "direct", "structured", "review", "repair"}
    if not isinstance(stages, dict) or set(stages) - allowed_stages:
        raise ValueError("model.stages 包含未知阶段或不是映射")
    height = cfg["run"].get("recognition_block_height", cfg["processing"]["block_height"])
    if type(height) is not int or height <= 0:
        raise ValueError("识别分块高度必须为正整数")
    refresh = cfg["run"].get("refresh_stages", [])
    if not isinstance(refresh, list) or any(not isinstance(s, str) or s not in allowed_stages for s in refresh):
        raise ValueError("run.refresh_stages 必须为合法模型阶段的列表")
    for name in ("reuse_answer_cache", "reuse_recognition", "accept_document_changes"):
        if type(cfg["run"].get(name, False)) is not bool:
            raise ValueError(f"run.{name} 必须为布尔值")
    for stage, opts in stages.items():
        if not isinstance(opts, dict) or set(opts) - {"timeout", "max_tokens", "enable_thinking", "thinking_budget", "json_object"}:
            raise ValueError(f"model.stages.{stage} 参数非法")
        if 'json_object' in opts and type(opts['json_object']) is not bool:
            raise ValueError(f'model.stages.{stage}.json_object必须为布尔值')
        if "timeout" in opts and (type(opts["timeout"]) not in (int, float) or not math.isfinite(opts["timeout"]) or opts["timeout"] <= 0):
            raise ValueError(f"model.stages.{stage}.timeout 必须为有限正数")
        for name in ("max_tokens", "thinking_budget"):
            if name in opts and (type(opts[name]) is not int or opts[name] <= 0):
                raise ValueError(f"model.stages.{stage}.{name} 必须为正整数")
        if "enable_thinking" in opts and type(opts["enable_thinking"]) is not bool:
            raise ValueError(f"model.stages.{stage}.enable_thinking 必须为布尔值")
        if "thinking_budget" in opts and opts.get("enable_thinking") is not True:
            raise ValueError("配置 thinking_budget 时必须显式启用 enable_thinking")
    if cfg["processing"].get("recognize_cell_format", "object") not in {"array", "object"}:
        raise ValueError("recognize_cell_format 必须为 array 或 object")
    for value in (cfg["run"]["min_review_confidence"], cfg["model"]["bbox_clip_tolerance"]):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("复核阈值与坐标容差必须为0到1的有限数")
    if cfg["model"]["bbox_clip_tolerance"] > .05:
        raise ValueError("坐标容差不得超过5%，严重越界应回退")
    if cfg["model"]["bbox_format"] not in {"pixel", "norm999", "norm1000", "resized_pixel"}:
        raise ValueError("未知 bbox_format")
    resize = cfg["model"]["bbox_resize"]
    if any(type(resize[k]) is not int or resize[k] <= 0 for k in ("factor", "min_pixels", "max_pixels")):
        raise ValueError("坐标缩放参数必须为正整数")
    if not resize["factor"] ** 2 <= resize["min_pixels"] <= resize["max_pixels"]:
        raise ValueError("坐标缩放像素上下限非法")
