"""GeminiProvider against a fake transport: no network, no real key."""

import json

import httpx
import pytest
from google.genai import errors as genai_errors

from app.services.llm.errors import (
    LLMAuthError,
    LLMInvalidResponse,
    LLMNotConfigured,
    LLMQuotaExceeded,
    LLMSafetyBlocked,
    LLMTimeout,
    LLMUpstreamError,
)
from app.services.llm.gemini import GeminiProvider, RawResponse
from tests.llm.fixtures import (
    FAKE_KEY,
    VALID_CANDIDATES_JSON,
    FakeTransport,
    anand_context,
    explain_request,
)

OK = RawResponse(text=VALID_CANDIDATES_JSON, finish_reason="STOP")


def make(transport: FakeTransport, retries: int = 2) -> GeminiProvider:
    return GeminiProvider(
        api_key=FAKE_KEY,
        model="gemini-test",
        max_retries=retries,
        transport=transport,
        sleep=lambda _s: None,
    )


def client_error(code: int, status: str, message: str = "boom") -> genai_errors.APIError:
    body = {"error": {"code": code, "status": status, "message": message}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


def test_missing_key_raises_not_configured() -> None:
    with pytest.raises(LLMNotConfigured, match="GEMINI_API_KEY"):
        GeminiProvider(api_key=None, model="gemini-test", transport=FakeTransport(OK))


def test_missing_model_raises_not_configured() -> None:
    with pytest.raises(LLMNotConfigured, match="GEMINI_MODEL"):
        GeminiProvider(api_key=FAKE_KEY, model=None, transport=FakeTransport(OK))


def test_success_sends_json_schema() -> None:
    t = FakeTransport(OK)
    result = make(t).generate_candidates(anand_context())
    assert result.candidates[0].discount_inr == 20
    schema = t.calls[0]["json_schema"]
    assert "candidates" in schema["properties"]
    assert "Anand Cafe" in t.calls[0]["prompt"]


def test_quota_retries_then_raises() -> None:
    t = FakeTransport(client_error(429, "RESOURCE_EXHAUSTED"))
    with pytest.raises(LLMQuotaExceeded):
        make(t, retries=2).ping()
    assert len(t.calls) == 3  # 1 try + 2 retries


def test_quota_then_success_recovers() -> None:
    t = FakeTransport(client_error(429, "RESOURCE_EXHAUSTED"), OK)
    assert make(t).generate_candidates(anand_context()).candidates
    assert len(t.calls) == 2


def test_server_error_maps_to_upstream() -> None:
    with pytest.raises(LLMUpstreamError):
        make(FakeTransport(client_error(503, "UNAVAILABLE")), retries=0).ping()


def test_timeout_maps_and_retries() -> None:
    t = FakeTransport(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeout):
        make(t, retries=1).ping()
    assert len(t.calls) == 2


def test_bad_key_not_retried() -> None:
    t = FakeTransport(client_error(403, "PERMISSION_DENIED"))
    with pytest.raises(LLMAuthError, match="GEMINI_API_KEY"):
        make(t).ping()
    assert len(t.calls) == 1


def test_unknown_model_points_to_setting() -> None:
    with pytest.raises(LLMAuthError, match="GEMINI_MODEL"):
        make(FakeTransport(client_error(404, "NOT_FOUND"))).ping()


@pytest.mark.parametrize(
    "raw",
    [
        RawResponse(text="not json"),
        RawResponse(text=""),
        RawResponse(text=json.dumps({"candidates": [{"campaign_type": "x"}]})),
        RawResponse(text=VALID_CANDIDATES_JSON, finish_reason="MAX_TOKENS"),
    ],
)
def test_invalid_responses_rejected(raw: RawResponse) -> None:
    t = FakeTransport(raw)
    with pytest.raises(LLMInvalidResponse):
        make(t).generate_candidates(anand_context())
    assert len(t.calls) == 1  # invalid output is not retried blindly


@pytest.mark.parametrize(
    "raw",
    [RawResponse(text=None, block_reason="SAFETY"), RawResponse(text="{}", finish_reason="SAFETY")],
)
def test_safety_blocks(raw: RawResponse) -> None:
    with pytest.raises(LLMSafetyBlocked):
        make(FakeTransport(raw)).ping()


def test_explanation_cannot_cite_unknown_records() -> None:
    bad = json.dumps({"text": "Campaign Z is best.", "lang": "en", "referenced_record_ids": ["Z"]})
    with pytest.raises(LLMInvalidResponse, match="record id"):
        make(FakeTransport(RawResponse(text=bad))).explain(explain_request("en"))


def test_explanation_language_must_match() -> None:
    raw = json.dumps({"text": "A is best.", "lang": "en", "referenced_record_ids": ["A"]})
    with pytest.raises(LLMInvalidResponse, match="language"):
        make(FakeTransport(RawResponse(text=raw))).explain(explain_request("hinglish"))


def test_valid_explanation() -> None:
    raw = json.dumps(
        {"text": "A is allowed; B blocked.", "lang": "en", "referenced_record_ids": ["A", "B"]}
    )
    exp = make(FakeTransport(RawResponse(text=raw))).explain(explain_request("en"))
    assert exp.referenced_record_ids == ["A", "B"]


def test_network_error_maps_to_upstream() -> None:
    with pytest.raises(LLMUpstreamError, match="network"):
        make(FakeTransport(httpx.ConnectError("refused")), retries=0).ping()
