class TransEngineError(Exception):
    """Base error for all TransEngine operations."""
    code: str = "pyron.error"
    retryable: bool = False

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InputError(TransEngineError):
    code = "input.invalid"


class ProviderError(TransEngineError):
    code = "provider.error"
    retryable = True


class BudgetExceeded(TransEngineError):
    code = "budget.exceeded"


class AlignmentError(TransEngineError):
    code = "align.failed"


class VerificationError(TransEngineError):
    code = "verify.failed"


class RenderError(TransEngineError):
    code = "render.failed"
