class SupplierError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class CurrencyError(ValueError):
    pass


class SearchExpiredError(LookupError):
    pass
