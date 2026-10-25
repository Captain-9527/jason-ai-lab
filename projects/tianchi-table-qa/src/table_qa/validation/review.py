"""复核风险分级：未知风险保守阻断，只有白名单提示可不重答。"""
from ..schemas import PipelineError


ADVISORY_CODES = {"evidence_granularity", "equivalent_representation", "resolved_ambiguity"}


def classify_risks(risks, policy="graded"):
    if not isinstance(risks, list):
        raise PipelineError("复核缺少 risks 数组")
    blocking, advisory = [], []
    for risk in risks:
        if isinstance(risk, str) and risk.strip():
            # 兼容旧响应，但不猜测自然语言的严重程度。
            blocking.append({"code": "legacy", "message": risk, "severity": "blocking"})
            continue
        if not isinstance(risk, dict) or risk.get("severity") not in {"blocking", "advisory"}:
            raise PipelineError("复核风险必须有 blocking/advisory 级别")
        if not all(isinstance(risk.get(k), str) and risk[k].strip() for k in ("code", "message")):
            raise PipelineError("复核风险缺少 code/message")
        safe = (policy == "graded" and risk["severity"] == "advisory"
                and risk["code"] in ADVISORY_CODES
                and isinstance(risk.get("rationale"), str) and bool(risk["rationale"].strip()))
        (advisory if safe else blocking).append(risk)
    return blocking, advisory
