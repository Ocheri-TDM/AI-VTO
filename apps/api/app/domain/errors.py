class SupplierError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, retry_after_seconds: float = 0,
                 evidence: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.evidence = evidence or {}


class CurrencyError(ValueError):
    pass


class SearchExpiredError(LookupError):
    pass
