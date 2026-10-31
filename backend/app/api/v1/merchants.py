"""Merchant profile and baseline analysis routes (read-only)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import EffectiveRepo
from app.db import get_session
from app.schemas.analytics import MerchantAnalysis
from app.schemas.settings import SettingsIn, SettingsOut
from app.services import merchant_settings
from app.services.analytics import analyze_merchant

router = APIRouter(prefix="/merchants", tags=["merchants"])

RepoDep = EffectiveRepo
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("")
def list_merchants(repo: RepoDep) -> dict[str, Any]:
    return {
        "data_source": repo.data_source,
        "is_synthetic": repo.is_synthetic,
        "merchants": repo.list_merchants(),
    }


@router.get("/{merchant_id}")
def get_merchant(merchant_id: str, repo: RepoDep) -> dict[str, Any]:
    return repo.merchant_profile(merchant_id)


@router.get("/{merchant_id}/baseline", response_model=MerchantAnalysis)
def get_baseline(merchant_id: str, repo: RepoDep) -> MerchantAnalysis:
    """Baseline, trends, data quality and opportunity alerts for one merchant."""
    return analyze_merchant(
        repo.orders_for(merchant_id),
        repo.merchant_info(merchant_id),
        repo.as_of,
        data_source=repo.data_source,
        is_synthetic=repo.is_synthetic,
        known_merchants=repo.merchant_ids,
    )


@router.get("/{merchant_id}/settings", response_model=SettingsOut)
def get_settings_route(merchant_id: str, repo: RepoDep, session: SessionDep) -> SettingsOut:
    """The merchant's limits, usual campaign costs and preferences, with the defaults."""
    return merchant_settings.get_settings_out(session, repo, merchant_id)


@router.put("/{merchant_id}/settings", response_model=SettingsOut)
def save_settings_route(
    merchant_id: str, body: SettingsIn, repo: RepoDep, session: SessionDep
) -> SettingsOut:
    """Save the merchant's settings. Changed limits or costs create a new policy version;
    campaigns evaluated before keep the version they were checked against."""
    return merchant_settings.save_settings(session, repo, merchant_id, body)
