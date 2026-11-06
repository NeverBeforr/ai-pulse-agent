"""命令行与适配器之间传递的明确错误。"""

from typing import Literal


class ConfigError(ValueError):
    """配置内容或环境变量不满足契约。"""


class SourceFailure(Exception):
    def __init__(
        self,
        kind: Literal["network", "http", "parse", "auth", "empty"],
        message: str,
        *,
        client_failure: bool = False,
    ):
        self.kind = kind
        self.client_failure = client_failure
        super().__init__(message)


class GenerationError(RuntimeError):
    """模型调用或结果校验失败。"""


class BudgetExceeded(GenerationError):
    """本轮预算已用尽，允许保留已完成阅读的条目。"""


class DeliveryError(RuntimeError):
    """简报没有被目标服务接受。"""
