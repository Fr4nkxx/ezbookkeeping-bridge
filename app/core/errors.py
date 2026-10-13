class BridgeError(Exception):
    """bridge 内部可预期错误的基类；流程层据此转换为 failed 结果。"""


class EzApiError(BridgeError):
    def __init__(self, message: str, *, error_code: int | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.path = path


class LlmError(BridgeError):
    pass


class PromptNotFoundError(BridgeError, FileNotFoundError):
    pass
