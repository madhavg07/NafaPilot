"""Recommendation runs, explanations and merchant preferences (M7)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import EffectiveRepo
from app.config import Settings, get_settings
from app.db import get_session
from app.schemas.recommendations import (
    ExplainRequestIn,
    ExplanationOut,
    PreferencesOut,
    RecommendationRequest,
    RecommendationRunOut,
)
from app.services import recommendations as recs
from app.services.llm import LLMProvider, get_provider

router = APIRouter(tags=["recommendations"])


def provider_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> LLMProvider:
    """Configured provider; raises LLMNotConfigured (503) if Gemini lacks a key/model."""
    return get_provider(settings)


RepoDep = EffectiveRepo
SessionDep = Annotated[Session, Depends(get_session)]
ProviderDep = Annotated[LLMProvider, Depends(provider_dependency)]


@router.post(
    "/merchants/{merchant_id}/recommendations",
    response_model=RecommendationRunOut,
    status_code=201,
)
def generate(
    merchant_id: str,
    body: RecommendationRequest,
    repo: RepoDep,
    session: SessionDep,
    provider: ProviderDep,
) -> RecommendationRunOut:
    """Generate candidates, evaluate them, pick by rule, explain, and store everything."""
    run_id = recs.generate_run(session, repo, provider, merchant_id, body)
    return recs.get_run(session, run_id)


@router.get("/recommendations/{run_id}", response_model=RecommendationRunOut)
def get_run(run_id: uuid.UUID, session: SessionDep) -> RecommendationRunOut:
    return recs.get_run(session, run_id)


@router.post(
    "/recommendations/{run_id}/explanations", response_model=ExplanationOut, status_code=201
)
def explain(
    run_id: uuid.UUID, body: ExplainRequestIn, session: SessionDep, provider: ProviderDep
) -> ExplanationOut:
    """Another explanation of a stored run (another language, or a merchant question)."""
    explanation_id = recs.explain_run(
        session, provider, run_id, body.lang, body.question, body.requested_by
    )
    run = recs.get_run(session, run_id)
    return next(e for e in run.explanations if e.explanation_id == explanation_id)


@router.get("/merchants/{merchant_id}/preferences", response_model=PreferencesOut)
def preferences(merchant_id: str, repo: RepoDep, session: SessionDep) -> PreferencesOut:
    repo.merchant_profile(merchant_id)  # 404 for unknown merchants
    return recs.preferences_out(session, merchant_id)
