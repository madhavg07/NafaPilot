"""The API key must never appear in logs, error messages, settings repr or API responses."""

import logging

import pytest
from google.genai import errors as genai_errors

from app.config import Settings
from app.logging_config import REDACTED, SecretRedactingFilter, redact
from app.services.llm.errors import LLMAuthError
from app.services.llm.factory import llm_status
from tests.llm.fixtures import FAKE_KEY, FakeTransport
from tests.llm.test_gemini_provider import make


def test_redact_helper() -> None:
    assert redact(f"key={FAKE_KEY}!", [FAKE_KEY]) == f"key={REDACTED}!"


def test_log_filter_scrubs_message_and_args(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("redaction-test")
    caplog.handler.addFilter(SecretRedactingFilter([FAKE_KEY]))
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        logger.info("calling with %s", FAKE_KEY)
        try:
            raise RuntimeError(f"failed {FAKE_KEY}")
        except RuntimeError:
            logger.exception("boom")
    assert FAKE_KEY not in caplog.text
    assert REDACTED in caplog.text


def test_settings_repr_hides_key() -> None:
    s = Settings(llm_provider="gemini", gemini_api_key=FAKE_KEY, gemini_model="m")
    assert FAKE_KEY not in repr(s)
    assert FAKE_KEY not in str(s.model_dump())


def test_status_never_contains_key() -> None:
    s = Settings(llm_provider="gemini", gemini_api_key=FAKE_KEY, gemini_model="m")
    status = llm_status(s)
    assert FAKE_KEY not in str(status)
    assert status["key_present"] is True and status["configured"] is True


def test_upstream_message_with_key_is_redacted() -> None:
    err = genai_errors.ClientError(
        400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"bad {FAKE_KEY}"}}
    )
    with pytest.raises(LLMAuthError) as exc_info:
        make(FakeTransport(err)).ping()
    assert FAKE_KEY not in str(exc_info.value)
    assert FAKE_KEY not in exc_info.value.message


def test_empty_key_counts_as_missing() -> None:
    s = Settings(llm_provider="gemini", gemini_api_key="   ", gemini_model="m")
    assert s.gemini_key_value is None
    assert llm_status(s)["configured"] is False
