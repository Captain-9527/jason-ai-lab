"""项目异常定义。"""


class AppError(Exception):
    """可向用户展示的业务异常。"""


class ConfigurationError(AppError):
    """配置错误。"""


class PathValidationError(AppError):
    """目录路径不符合安全规则。"""


class ScanLimitError(AppError):
    """扫描超出限制。"""


class SearchProviderError(AppError):
    """搜索源调用失败。"""


class SearchProviderAuthError(SearchProviderError):
    """搜索源认证或权限失败，不应盲目重试。"""


class TraceWriteError(AppError):
    """执行追踪无法写入。"""

