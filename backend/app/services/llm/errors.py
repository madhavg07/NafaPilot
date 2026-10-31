"""Typed LLM errors. Each maps to an HTTP status and a stable error_code for the API.

Messages are written by us and never include the API key or raw upstream payloads.
"""

from __future__ import annotations


class LLMError(Exception):
    error_code: str = "llm_error"
    http_status: int = 502

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LLMNotConfigured(LLMError):
    error_code = "llm_not_configured"
    http_status = 503


class LLMQuotaExceeded(LLMError):
    error_code = "llm_quota_exceeded"
    http_status = 429


class LLMTimeout(LLMError):
    error_code = "llm_timeout"
    http_status = 504


class LLMUpstreamError(LLMError):
    error_code = "llm_upstream_error"
    http_status = 502


class LLMAuthError(LLMError):
    """Key rejected (401/403) or bad request (400) — retrying will not help."""

    error_code = "llm_auth_or_request_error"
    http_status = 502


class LLMInvalidResponse(LLMError):
    error_code = "llm_invalid_response"
    http_status = 502


class LLMSafetyBlocked(LLMError):
    error_code = "llm_safety_blocked"
    http_status = 502
