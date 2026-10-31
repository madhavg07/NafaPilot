"""Sarvam provider (fake HTTP, no network) and the Sarvam -> Gemini fallback chain."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.services.llm.errors import (
    LLMAuthError,
    LLMInvalidResponse,
    LLMNotConfigured,
    LLMQuotaExceeded,
)
from app.services.llm.factory import get_provider, llm_status
from app.services.llm.fallback import FallbackProvider
from app.services.llm.gemini import GeminiProvider, RawResponse
from app.services.llm.sarvam import SarvamProvider, SarvamTransport
from tests.llm.fixtures import VALID_CANDIDATES_JSON, FakeTransport, anand_context

SARVAM_KEY = "sk_FAKE-sarvam-key-abcdef0123456789"


def sarvam(handler: Any, retries: int = 0) -> tuple[SarvamProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)  # type: ignore[no-any-return]

    transport = SarvamTransport(
        SARVAM_KEY,
        5,
        reasoning_effort="low",
        client=httpx.Client(transport=httpx.MockTransport(record)),
    )
    provider = SarvamProvider(
        api_key=SARVAM_KEY,
        model="sarvam-105b",
        max_retries=retries,
        transport=transport,
        sleep=lambda _s: None,
    )
    return provider, seen


def ok(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content},
                }
            ]
        },
    )


def test_request_shape_and_success() -> None:
    p, seen = sarvam(lambda _r: ok("```json\n" + VALID_CANDIDATES_JSON + "\n```"))
    result = p.generate_candidates(anand_context())
    assert len(result.candidates) >= 1
    req = seen[0]
    assert str(req.url) == "https://api.sarvam.ai/v2/chat/completions"
    assert req.headers["api-subscription-key"] == SARVAM_KEY
    body = json.loads(req.content)
    assert body["model"] == "sarvam-105b"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "nafapilot_output"
    assert "properties" in body["response_format"]["json_schema"]["schema"]
    assert body["reasoning_effort"] == "low"
    assert p.name == "sarvam"


@pytest.mark.parametrize(
    ("status", "error"),
    [(403, LLMAuthError), (401, LLMAuthError), (404, LLMAuthError), (429, LLMQuotaExceeded)],
)
def test_http_errors_map_and_never_leak_the_key(status: int, error: type[Exception]) -> None:
    p, _ = sarvam(
        lambda _r: httpx.Response(
            status, json={"error": {"message": f"bad key {SARVAM_KEY}", "code": "x"}}
        )
    )
    with pytest.raises(error) as info:
        p.ping()
    assert SARVAM_KEY not in str(info.value)


def test_overloaded_is_retried_then_succeeds() -> None:
    calls = iter(
        [httpx.Response(503, json={"error": {"message": "model_overloaded"}}), ok('{"ok": true}')]
    )
    p, seen = sarvam(lambda _r: next(calls), retries=1)
    p.ping()
    assert len(seen) == 2


def test_cut_off_and_non_json_are_invalid() -> None:
    p, _ = sarvam(lambda _r: ok('{"ok": tr', finish="length"))
    with pytest.raises(LLMInvalidResponse, match="MAX_TOKENS"):
        p.ping()
    p, _ = sarvam(lambda _r: ok("Sure! Here is my answer."))
    with pytest.raises(LLMInvalidResponse):
        p.ping()


def test_missing_key_is_not_configured() -> None:
    with pytest.raises(LLMNotConfigured, match="SARVAM_API_KEY"):
        SarvamProvider(api_key="", model="sarvam-105b")


# ------------------------------------------------------------------ fallback chain


def _gemini(*steps: Any) -> GeminiProvider:
    return GeminiProvider(
        api_key="AIzaFAKE",
        model="gemini-test",
        max_retries=0,
        transport=FakeTransport(*steps),
        sleep=lambda _s: None,
    )


def test_falls_back_to_gemini_and_records_who_answered() -> None:
    failing, _ = sarvam(
        lambda _r: httpx.Response(429, json={"error": {"message": "budget exhausted"}})
    )
    chain = FallbackProvider([failing, _gemini(RawResponse(text=VALID_CANDIDATES_JSON))])
    result = chain.generate_candidates(anand_context())
    assert result.candidates
    assert chain.name == "gemini" and chain.model == "gemini-test"
    assert chain.attempts == [("sarvam", "llm_quota_exceeded"), ("gemini", "ok")]


def test_uses_sarvam_when_it_works() -> None:
    good, _ = sarvam(lambda _r: ok(VALID_CANDIDATES_JSON))
    chain = FallbackProvider([good, _gemini()])  # Gemini would fail if called (no responses)
    chain.generate_candidates(anand_context())
    assert chain.name == "sarvam" and chain.attempts == [("sarvam", "ok")]


def test_all_failing_raises_with_a_summary() -> None:
    bad, _ = sarvam(lambda _r: httpx.Response(403, json={"error": {"message": "invalid"}}))
    chain = FallbackProvider([bad, _gemini(RawResponse(text="not json"))])
    with pytest.raises(
        LLMInvalidResponse, match="All AI providers failed .*sarvam: llm_auth_or_request_error"
    ):
        chain.ping()


def test_factory_builds_chain_and_skips_unconfigured_members() -> None:
    s = Settings(
        llm_provider="sarvam,gemini", sarvam_api_key="", gemini_api_key="AIzaFAKE", gemini_model="g"
    )
    provider = get_provider(s)
    assert isinstance(provider, FallbackProvider) and provider.chain == ["sarvam", "gemini"]
    status = llm_status(s)
    assert status["provider"] == "sarvam" and status["configured"] is True
    chain = status["chain"]
    assert isinstance(chain, list)
    assert [(m["provider"], m["configured"]) for m in chain] == [
        ("sarvam", False),
        ("gemini", True),
    ]
    assert "AIzaFAKE" not in json.dumps(status)


def test_factory_with_nothing_configured_says_so() -> None:
    s = Settings(
        llm_provider="sarvam,gemini", sarvam_api_key="", gemini_api_key="", gemini_model="g"
    )
    with pytest.raises(LLMNotConfigured, match="No AI provider"):
        get_provider(s)


def test_invalid_provider_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(llm_provider="sarvam,openai")
    with pytest.raises(ValueError):
        Settings(llm_provider="gemini,gemini")
    assert Settings(llm_provider=" Sarvam , Gemini ").llm_provider_chain == ["sarvam", "gemini"]
