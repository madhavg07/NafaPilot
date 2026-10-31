"""Product-level offers: catalogue, product selection, offer runs, basket check, edits."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import EffectiveRepo
from app.api.v1.recommendations import ProviderDep
from app.db import get_session
from app.schemas.campaigns import CampaignOut
from app.schemas.promotions import BasketCheckIn, BasketCheckOut, CatalogueOut, SelectionIn
from app.schemas.recommendations import PromotionEditIn, PromotionRunRequest, RecommendationRunOut
from app.services import campaign_store as store
from app.services import recommendations as recs
from app.services.promotions import service
from app.services.promotions.catalogue import catalogue_out
from app.services.promotions.recommend import generate_promotion_run
from app.services.promotions.selection import save_selection

router = APIRouter(tags=["offers"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/merchants/{merchant_id}/products", response_model=CatalogueOut)
def products(merchant_id: str, repo: EffectiveRepo) -> CatalogueOut:
    """Product catalogue with 28-day demand, margins, co-purchase pairs and the selection."""
    return catalogue_out(repo, merchant_id)


@router.put("/merchants/{merchant_id}/products/selection", response_model=CatalogueOut)
def select_products(
    merchant_id: str, body: SelectionIn, repo: EffectiveRepo, session: SessionDep
) -> CatalogueOut:
    """Save which products may appear in offers (a new version if it changed)."""
    out, _ = save_selection(session, repo, merchant_id, body)
    return out


@router.post(
    "/merchants/{merchant_id}/offers/recommendations",
    response_model=RecommendationRunOut,
    status_code=201,
)
def recommend_offers(
    merchant_id: str,
    body: PromotionRunRequest,
    repo: EffectiveRepo,
    session: SessionDep,
    provider: ProviderDep,
) -> RecommendationRunOut:
    """The AI proposes product, bundle and cart offers on the selected products; code
    evaluates whole baskets, applies policy and picks one by a fixed rule."""
    run_id = generate_promotion_run(session, repo, provider, merchant_id, body)
    return recs.get_run(session, run_id)


@router.post("/merchants/{merchant_id}/baskets/check", response_model=BasketCheckOut)
def check_basket(
    merchant_id: str, body: BasketCheckIn, repo: EffectiveRepo, session: SessionDep
) -> BasketCheckOut:
    """Show exactly which discounts one basket would get from the given offers."""
    return service.check_basket(session, repo, merchant_id, body)


@router.post("/campaigns/{campaign_id}/offer-versions", response_model=CampaignOut, status_code=201)
def edit_offer(
    campaign_id: uuid.UUID, body: PromotionEditIn, repo: EffectiveRepo, session: SessionDep
) -> CampaignOut:
    """Change an offer's discount (or a cart offer's minimum value). Code rebuilds the
    inputs from the order history; the change is a new version that needs a new approval."""
    service.edit_offer(session, repo, campaign_id, body)
    return store.get_campaign(session, campaign_id)
