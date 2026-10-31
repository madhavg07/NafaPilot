"""Basket check and offer edits for product-level campaigns."""

from __future__ import annotations

import uuid

import pandas as pd
from sqlalchemy.orm import Session

from app.data import DataRepository
from app.schemas.campaigns import VersionCreate
from app.schemas.promotions import BasketCheckIn, BasketCheckOut, PromotionSpec
from app.schemas.recommendations import PromotionEditIn
from app.services import campaign_store as store
from app.services.campaign_store import CampaignError
from app.services.promotions.catalogue import merchant_cost_ratio, products_map
from app.services.promotions.engine import basket_check, build_input, label_for
from app.services.promotions.selection import UnknownProducts


class NotAnOffer(CampaignError):
    http_status, error_code = 422, "not_an_offer"


def check_basket(
    session: Session, repo: DataRepository, merchant_id: str, body: BasketCheckIn
) -> BasketCheckOut:
    products = products_map(repo, merchant_id)
    unknown = sorted({line.sku for line in body.lines} - set(products))
    if unknown:
        raise UnknownProducts(f"Unknown product(s): {', '.join(unknown)}.", {"skus": unknown})
    offers: list[tuple[str | None, PromotionSpec]] = []
    for cid in body.campaign_ids:
        c = store.get_campaign(session, uuid.UUID(cid))
        promo = c.current.inputs.promotion
        if c.merchant_id != merchant_id or promo is None:
            raise NotAnOffer(f"Campaign {cid} is not a product, bundle or cart offer of this shop.")
        offers.append((cid, promo))
    _, _, selected = repo.product_selection(merchant_id)
    qty: dict[str, int] = {}
    for line in body.lines:
        qty[line.sku] = qty.get(line.sku, 0) + line.quantity
    return basket_check(sorted(qty.items()), offers, products, selected)


def edit_offer(
    session: Session, repo: DataRepository, campaign_id: uuid.UUID, body: PromotionEditIn
) -> uuid.UUID:
    c = store.get_campaign(session, campaign_id)
    inp = c.current.inputs
    promo = inp.promotion
    if promo is None:
        raise NotAnOffer("This campaign is not a product, bundle or cart offer.")
    if promo.kind == "cart" and body.min_cart_value_inr is None:
        body = body.model_copy(update={"min_cart_value_inr": promo.min_cart_value_inr})
    if promo.kind != "cart" and body.min_cart_value_inr is not None:
        raise NotAnOffer("Only a cart offer has a minimum cart value.")
    _, _, selected = repo.product_selection(c.merchant_id)
    products = products_map(repo, c.merchant_id)
    spec = PromotionSpec(
        kind=promo.kind,
        skus=list(promo.skus),
        discount_inr=body.discount_inr,
        min_cart_value_inr=body.min_cart_value_inr if promo.kind == "cart" else None,
        hours=promo.hours,
        weekdays_only=promo.weekdays_only,
        eligible_skus=list(selected) if promo.kind == "cart" else [],
    )
    spec = spec.model_copy(update={"label": label_for(spec, products)})
    new_inp, _ = build_input(
        spec,
        campaign_ref=inp.campaign_ref,
        products=products,
        orders=repo.orders_for(c.merchant_id),
        items=repo.items_for(c.merchant_id),
        as_of=pd.Timestamp(repo.as_of),
        incremental={name: int(a.incremental_orders or 0) for name, a in inp.scenarios.items()},
        campaign_cost_inr=(
            body.campaign_cost_inr if body.campaign_cost_inr is not None else inp.campaign_cost_inr
        ),
        merchant_cost_ratio=merchant_cost_ratio(repo, c.merchant_id),
        assumption_source="merchant_edit",
        is_synthetic=inp.is_synthetic,
    )
    return store.create_version(
        session,
        repo,
        campaign_id,
        VersionCreate(campaign=new_inp, created_by=body.created_by, change_note=body.change_note),
    )
