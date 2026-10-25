"""唯一云端入口：图片传输、请求缓存、限流、预算与失败分类。"""
import base64
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from ..schemas import BudgetExceeded, PipelineError, RunAborted
from ..validation.coordinates import coordinate_contract, reference_size
from .storage import Cache, atomic_json, digest, file_hash
from .tracing import redact


def parse_json(raw):
    text = str(raw).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        def unique_object(pairs):
            value = {}
            for key,item in pairs:
                if key in value:
                    raise ValueError('duplicate_json_key')
                value[key] = item
            return value
        value = json.loads(text, object_pairs_hook=unique_object,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except ValueError as exc:
        if str(exc) == 'duplicate_json_key':
            raise PipelineError('模型JSON包含重复字段，禁止静默覆盖上层表头或其他事实') from exc
        raise PipelineError("模型未返回合法 JSON") from exc
    if not isinstance(value, dict):
        raise PipelineError("模型响应应为 JSON 对象")
    return value


def prompt_data(value):
    """不将运行目录路径发给模型或纳入缓存；证据页码/坐标仍保留。"""
    if isinstance(value, dict):
        return {k: prompt_data(v) for k, v in value.items() if k not in {
            "image", "original", "reasoning_content", "reasoning", "chain_of_thought",
            "review_history", "raw_response"}}
    if isinstance(value, list):
        return [prompt_data(v) for v in value]
    return value


class ModelClient:
    def __init__(self, cfg, trace, run_dir, used_calls=0):
        from openai import OpenAI
        key = os.getenv("DASHSCOPE_API_KEY")
        if not key or not cfg["model"]["vision"]:
            raise ValueError("请配置 DASHSCOPE_API_KEY 和 QWEN_VL_MODEL（或 model.vision）")
        self.cfg, self.trace = cfg, trace
        self.client = OpenAI(api_key=key, base_url=cfg["model"]["base_url"],
                             timeout=cfg["model"]["timeout"], max_retries=0)
        self.cache = Cache(Path(cfg["paths"]["cache"]) / "model")
        self.artifacts = Path(run_dir) / "artifacts/model"
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.local = threading.local()
        self.used_calls, self.next_request = used_calls, 0.0

    def set_document_refresh(self, enabled):
        self.local.refresh_recognition = bool(enabled)

    def reset_question_budget(self):
        self.local.calls = 0

    def cancel(self):
        """不再发起新请求；已经发出的请求受 timeout 约束。"""
        self.cancelled.set()

    def reserve(self):
        with self.lock:
            if self.cancelled.is_set():
                raise RunAborted("本次运行已停止发起新请求")
            if self.used_calls >= self.cfg["run"]["max_model_calls"]:
                raise BudgetExceeded("本次运行模型请求预算已耗尽")
            if getattr(self.local, "calls", 0) >= self.cfg["run"]["max_calls_per_question"]:
                raise BudgetExceeded("当前题目/文档阶段请求预算已耗尽")
            self.used_calls += 1
            self.local.calls = getattr(self.local, "calls", 0) + 1
            now = time.monotonic()
            slot = max(now, self.next_request)
            self.next_request = slot + 60 / self.cfg["model"]["requests_per_minute"]
        time.sleep(max(0, slot - time.monotonic()))
        if self.cancelled.is_set():
            raise RunAborted("本次运行已停止发起新请求")

    def ask(self, stage, payload, images=None, vision=True, fresh=False):
        if stage == "review" and self.cfg["run"].get("single_pass", False):
            raise PipelineError("single_pass禁止模型复核调用")
        payload = prompt_data(payload)
        fresh = (fresh or self.cfg["run"].get("refresh_model_cache", False)
                 or stage in self.cfg["run"].get("refresh_stages", [])
                 or (getattr(self.local, "refresh_recognition", False)
                     and stage in {"locate", "blocks", "recognize", "boundary"}))
        images = [str(path) for path in (images or [])]
        cfg = self.cfg["model"]
        # 每次请求解析阶段参数，避免并发线程修改共享 SDK 的默认选项。
        options = cfg.get("stages", {}).get(stage, {})
        timeout = options.get("timeout", cfg["timeout"])
        max_tokens = options.get("max_tokens", cfg["max_tokens"])
        extra_body = {k: options[k] for k in ("enable_thinking", "thinking_budget") if k in options}
        response_format = {'type': 'json_object'} if options.get('json_object', False) else None
        if stage in {"locate", "blocks", "direct", "repair", "review"}:
            payload = {**payload, "coordinate_contract": coordinate_contract(self.cfg)}
            if payload.get('single_pass') and stage in {'direct', 'repair'}:
                payload['coordinate_contract']['evidence_declaration'] = {
                    'bbox_format': '每条evidence及row/column anchor显式填写pixel（推荐）/norm999/norm1000',
                    'image_size': '当前pages上传视图的[width,height]整数，不是原始整页或服务端缩放尺寸',
                    'rule': '显式声明仅覆盖该条证据；pixel直接写上传图像素，无需归一化。未声明时严格沿用format，不自动猜测'}
            if payload.get('single_pass') and any(v.get('local_view') for v in payload.get('input_views', [])):
                payload['coordinate_contract']['reference'] = '当前输入图片自身坐标；程序负责视图旋转和裁剪逆变换，禁止猜测原始整页坐标'
            if images:
                from PIL import Image
                frames = []
                for index, path in enumerate(images, 1):
                    with Image.open(path) as im:
                        w, h = im.size
                    rw, rh = reference_size(w, h, cfg) if cfg["bbox_format"] == "resized_pixel" else (w, h)
                    frames.append({"image_index": index, "uploaded_size": [w, h], "pixel_reference_size": [rw, rh]})
                payload["coordinate_frames"] = frames
        prompt_name = stage
        if (self.cfg['run'].get('single_pass', False) and stage in {'direct', 'repair'}
                and isinstance(payload.get('question'), dict) and payload['question'].get('answer_contract') == 'table'):
            prompt_name = stage + '_structure'
        prompt = self.cfg['prompts'].get(prompt_name, self.cfg['prompts'][stage])
        if payload.get('single_pass') and payload.get('response_mode'):
            prompt = self.cfg['prompts']['facts_response']
            if payload['response_mode'] == 'table_facts':
                prompt = self.cfg['prompts'].get('table_facts_response', prompt)
            elif payload['response_mode'] == 'header_facts':
                prompt = self.cfg['prompts'].get('header_facts_response', prompt)
        elif payload.get('single_pass') and any(v.get('caption_focused') for v in payload.get('input_views', [])):
            prompt = self.cfg['prompts'].get('direct_caption', prompt)
        elif payload.get('single_pass') and payload.get('task_requirements', {}).get('requested_fields'):
            prompt = self.cfg['prompts'].get('direct_fields', prompt)
        elif payload.get('single_pass') and payload.get('visual_task'):
            prompt = self.cfg['prompts'].get('direct_visual', prompt)
        model = cfg["vision"] if vision else cfg["text"]
        cache_input = {"stage": stage, "system": self.cfg["prompts"]["system"], "prompt": prompt,
                      "payload": payload, "images": [file_hash(p) for p in images], "model": model,
                      "base_url": cfg["base_url"], "temperature": cfg["temperature"], "tokens": max_tokens}
        if extra_body:
            cache_input["extra_body"] = extra_body
        if response_format:
            cache_input['response_format'] = response_format
        if images and self.cfg['run'].get('single_pass', False):
            cache_input['input_order'] = 'images_first'
        # 超时只影响传输，不使合法响应缓存失效；无语义覆盖时保持旧键兼容。
        key = digest(cache_input)
        with self.trace.stage(stage, {"payload": payload, "images": images, "model": model}) as output:
            if not fresh:
                cached = self.cache.get(key)
                if cached is not None:
                    self.trace.emit("cache_hit", stage=stage, key=key)
                    output["value"] = cached
                    atomic_json(self.artifacts / f"{key}.json", redact(cached))
                    return cached
            content = [{"type": "text", "text": prompt + "\n输入数据：\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]
            for path in images:
                data = base64.b64encode(Path(path).read_bytes()).decode()
                content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}})
            if images and self.cfg['run'].get('single_pass', False):
                content = content[1:] + content[:1]
            for attempt in range(cfg["retries"] + 1):
                self.reserve()
                request_id = uuid.uuid4().hex
                self.trace.emit("model_attempt", stage=stage, request_id=request_id, attempt=attempt + 1,
                                timeout=timeout, max_tokens=max_tokens, extra_body=extra_body)
                start = time.monotonic()
                try:
                    response = self.client.chat.completions.create(
                        model=model, temperature=cfg["temperature"], max_tokens=max_tokens,
                        timeout=timeout, **({"extra_body": extra_body} if extra_body else {}),
                        **({'response_format': response_format} if response_format else {}),
                        messages=[{"role": "system", "content": self.cfg["prompts"]["system"]},
                                  {"role": "user", "content": content}])
                except Exception as exc:
                    from openai import APIConnectionError, APITimeoutError
                    status = getattr(exc, "status_code", None)
                    retryable = isinstance(exc, (APIConnectionError, APITimeoutError)) or status == 429 or (status and status >= 500)
                    self.trace.emit("model_error", stage=stage, request_id=request_id, error_type=type(exc).__name__,
                                    status_code=status, duration_seconds=time.monotonic() - start)
                    if status in {401, 403, 404}:
                        self.cancel()
                        raise RunAborted(f"模型认证、权限或模型名称错误：HTTP={status}") from None
                    if not retryable or attempt == cfg["retries"]:
                        # 不把服务端完整请求体和认证头拼进异常。
                        raise PipelineError(f"模型请求失败：{type(exc).__name__} HTTP={status}") from None
                    time.sleep(min(8, 2 ** attempt))
                    continue
                raw = response.choices[0].message.content or ""
                self.trace.emit("model_response", stage=stage, request_id=request_id, model=model,
                                duration_seconds=time.monotonic() - start,
                                usage=response.usage.model_dump() if response.usage else {},
                                finish_reason=response.choices[0].finish_reason)
                atomic_json(self.artifacts / f"{request_id}.json", {"stage": stage, "raw": redact(raw)})
                if response.choices[0].finish_reason == "length":
                    raise PipelineError("模型输出被截断，需分块或调高 max_tokens")
                value = parse_json(raw)
                self.cache.put(key, value)
                output["value"] = value
                return value
        raise PipelineError("模型未返回结果")
