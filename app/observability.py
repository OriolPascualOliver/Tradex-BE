import logging
import json
import os
from contextvars import ContextVar


class Counter:
    """Minimal Prometheus-style counter."""

    def __init__(self, name: str, documentation: str):
        self.name = name
        self.documentation = documentation
        self.value = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount

    def render(self) -> str:
        return (
            f"# HELP {self.name} {self.documentation}\n"
            f"# TYPE {self.name} counter\n"
            f"{self.name} {self.value}\n"
        )


# Context variable to store correlation id per request
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


class JSONFormatter(logging.Formatter):
    """Simple JSON log formatter including correlation id."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting
        data = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", ""),
        }
        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "correlation_id",
                "message",
            }:
                data[key] = value
        return json.dumps(data)


class CorrelationIdFilter(logging.Filter):
    """Inject correlation id from context into log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        record.correlation_id = correlation_id_ctx.get("")
        return True


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logging.getLogger().addFilter(CorrelationIdFilter())

logger = logging.getLogger("observability")

# Counters
LOGIN_FAILURE_COUNTER = Counter(
    "login_failures_total", "Number of failed login attempts"
)
HTTP_403_COUNTER = Counter(
    "http_403_total", "Number of HTTP 403 responses"
)
HTTP_429_COUNTER = Counter(
    "http_429_total", "Number of HTTP 429 responses"
)
INVOICE_VERIFICATION_COUNTER = Counter(
    "invoice_verifications_total", "Number of invoice verification requests"
)
OPENAI_QUOTA_COUNTER = Counter(
    "openai_requests_total", "Number of requests sent to OpenAI"
)

COUNTERS = [
    LOGIN_FAILURE_COUNTER,
    HTTP_403_COUNTER,
    HTTP_429_COUNTER,
    INVOICE_VERIFICATION_COUNTER,
    OPENAI_QUOTA_COUNTER,
]

THRESHOLDS = {
    "login_failures_total": int(os.getenv("LOGIN_FAILURE_ALERT_THRESHOLD", "0")),
    "http_403_total": int(os.getenv("HTTP_403_ALERT_THRESHOLD", "0")),
    "http_429_total": int(os.getenv("HTTP_429_ALERT_THRESHOLD", "0")),
    "invoice_verifications_total": int(os.getenv("INVOICE_VERIFICATION_ALERT_THRESHOLD", "0")),
    "openai_requests_total": int(os.getenv("OPENAI_QUOTA_ALERT_THRESHOLD", "0")),
}


def _check_threshold(name: str, value: float) -> None:
    threshold = THRESHOLDS.get(name) or 0
    if threshold and value >= threshold:
        logger.warning(f"{name} threshold {threshold} reached")


def inc_login_failure() -> None:
    LOGIN_FAILURE_COUNTER.inc()
    _check_threshold("login_failures_total", LOGIN_FAILURE_COUNTER.value)


def inc_http_403() -> None:
    HTTP_403_COUNTER.inc()
    _check_threshold("http_403_total", HTTP_403_COUNTER.value)


def inc_http_429() -> None:
    HTTP_429_COUNTER.inc()
    _check_threshold("http_429_total", HTTP_429_COUNTER.value)


def inc_invoice_verification() -> None:
    INVOICE_VERIFICATION_COUNTER.inc()
    _check_threshold(
        "invoice_verifications_total", INVOICE_VERIFICATION_COUNTER.value
    )


def inc_openai_request() -> None:
    OPENAI_QUOTA_COUNTER.inc()
    _check_threshold("openai_requests_total", OPENAI_QUOTA_COUNTER.value)


def generate_metrics() -> bytes:
    return "".join(counter.render() for counter in COUNTERS).encode()


CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"


__all__ = [
    "correlation_id_ctx",
    "inc_login_failure",
    "inc_http_403",
    "inc_http_429",
    "inc_invoice_verification",
    "inc_openai_request",
    "generate_metrics",
    "CONTENT_TYPE_LATEST",
    "logger",
]
