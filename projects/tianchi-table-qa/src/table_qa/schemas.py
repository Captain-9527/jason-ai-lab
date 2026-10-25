"""跨层数据协议；模型 JSON 在使用前由 validation 层验证。"""
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

TYPES = {"structure", "extract", "thinking"}
FORMATS = {"json", "string", "number", "json_array"}


@dataclass(frozen=True)
class Question:
    id: str
    file_name: str
    question_type: str
    question: str
    table_hint: str = ""
    answer_format: str = ""
    route: str = ""  # 运行时语义类别，不改变原始题目清单

    def public(self) -> dict:
        # 题号与文件名不进入解题提示，防止成为答案线索。
        return {k: v for k, v in asdict(self).items() if k not in {"id", "file_name"}}


@dataclass
class AnswerResult:
    id: str
    status: str
    answer: Any = None
    evidence: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    route: str = ""
    inherited_from: str | None = None


class Model(Protocol):
    """用同一接口替换真实客户端与测试模型。"""
    def ask(self, stage: str, payload: dict, images: list[str] | None = None,
            vision: bool = True, fresh: bool = False) -> dict: ...


class PipelineError(ValueError):
    """可报告、可按预算恢复的数据或模型输出问题。"""


class BudgetExceeded(RuntimeError):
    """预算耗尽不自动重试。"""


class RunAborted(RuntimeError):
    """认证或配置错误应停止整批，避免连续无效调用。"""
