"""Logging with secret redaction.

Every handler on the root logger gets a filter that replaces known secret values
(currently the Gemini API key) with "[REDACTED]" before anything is written.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

REDACTED = "[REDACTED]"


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each secret in text."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # Render the message once with its args, redact it, then drop the args.
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(
                logging.Formatter().formatException(record.exc_info), self._secrets
            )
            record.exc_info = None
        return True


def configure_logging(secrets: Iterable[str], level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redactor = SecretRedactingFilter(list(secrets))
    for handler in root.handlers:
        handler.addFilter(redactor)
    # HTTP client libraries log request details at INFO/DEBUG; keep them quiet.
    for noisy in ("httpx", "httpx2", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
