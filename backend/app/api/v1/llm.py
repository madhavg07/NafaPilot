"""LLM status and connectivity routes."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.services.llm import LLMError, LLMProvider, get_provider, llm_status

router = APIRouter(prefix="/llm", tags=["llm"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _build_provider(settings: Settings) -> LLMProvider:
    """Raises LLMNotConfigured when Gemini is selected but not set up."""
    return get_provider(settings)


@router.get("/status")
def status(settings: SettingsDep) -> dict[str, object]:
    """Which provider is configured. No network call; never returns the key."""
    return llm_status(settings)


@router.post("/ping", response_model=None)
def ping(settings: SettingsDep) -> dict[str, Any] | JSONResponse:
    """Minimal live round-trip to the configured provider. Disabled in production."""
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")
    info = llm_status(settings)
    started = time.monotonic()
    try:
        provider = _build_provider(settings)
        provider.ping()
    except LLMError as err:
        return JSONResponse(
            status_code=err.http_status,
            content={
                "ok": False,
                "provider": info["provider"],
                "model": info["model"],
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_code": err.error_code,
                "message": err.message,
            },
        )
    return {
        "ok": True,
        "provider": provider.name,
        "model": provider.model,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
