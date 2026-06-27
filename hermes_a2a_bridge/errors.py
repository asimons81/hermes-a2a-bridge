"""Small, intentionally boring error hierarchy."""


class BridgeError(Exception):
    """Base error safe for conversion to a client-facing message."""


class ConfigError(BridgeError):
    pass


class ClientError(BridgeError):
    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.code = payload.get("code") if isinstance(payload, dict) else None
        error = payload.get("error") if isinstance(payload, dict) else None
        if self.code is None and isinstance(error, dict):
            for detail in error.get("details", []):
                if isinstance(detail, dict) and detail.get("reason"):
                    self.code = str(detail["reason"]).lower()
                    break
            if self.code is None and isinstance(error.get("status"), str):
                self.code = error["status"].lower()


class ExecutorError(BridgeError):
    pass


class ExecutorCanceled(ExecutorError):
    pass


class DatabaseBusyError(BridgeError):
    """A SQLite write stayed busy after the configured bounded retries."""
