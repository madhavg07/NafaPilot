"""Product-level offers through the API: selection, AI offer runs, basket check, edits,
approval and a simulated experiment."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.recommendations import provider_dependency
from app.db.models import AuditEvent, MerchantProductSelection
from app.main import app
from app.schemas.llm import ExtraOrders, PromotionCandidate, PromotionCandidateSet, PromotionContext
from app.services.llm.mock import MockProvider

M = "/api/v1/merchants/M001"
C = "/api/v1/campaigns"


def run(client: TestClient, **body: Any) -> Any:
    r = client.post(f"{M}/offers/recommendations", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_catalogue_lists_products_pairs_and_default_selection(client: TestClient) -> None:
    c = client.get(f"{M}/products").json()
    assert 15 <= len(c["products"]) <= 20
    assert c["selection_source"] == "default" and c["selection_version"] == 0
    assert all(p["selected"] for p in c["products"])
    capp = next(p for p in c["products"] if p["sku"] == "CF001")
    assert capp["price_inr"] == 180 and capp["margin_inr"] > 0 and capp["stats"]["units_28d"] > 0
    assert c["pairs"] and c["basket"]["orders_28d"] > 0
    assert client.get("/api/v1/merchants/M999/products").status_code == 404


def test_selection_is_versioned_and_append_only(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    r = client.put(f"{M}/products/selection", json={"skus": ["CF001", "SW001", "CD001", "CH001"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selection_version"] == 1 and body["selected_skus"] == [
        "CD001",
        "CF001",
        "CH001",
        "SW001",
    ]
    # Same set again: no new version.
    again = client.put(
        f"{M}/products/selection", json={"skus": ["SW001", "CH001", "CD001", "CF001"]}
    )
    assert again.json()["selection_version"] == 1
    bad = client.put(f"{M}/products/selection", json={"skus": ["ZZ999"]})
    assert bad.status_code == 422 and bad.json()["error_code"] == "unknown_products"
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(MerchantProductSelection)) == 1
        ev = s.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "product_selection_updated")
        ).one()
        assert "BK001" in ev.payload["removed"]
    with sessions() as s, pytest.raises(DBAPIError, match="append-only"), s.begin():
        s.execute(text("UPDATE merchant_product_selections SET version = 9"))


def test_offer_run_evaluates_product_bundle_and_cart_offers(client: TestClient) -> None:
    r = run(client)
    assert r["context"]["mode"] == "promotion" and r["provider"] == "mock"
    kinds = {c["promotion"]["kind"] for c in r["candidates"]}
    assert kinds == {"product", "bundle", "cart"}
    for c in r["candidates"]:
        p = c["promotion"]
        assert c["status"] == "evaluated" and c["campaign_id"] and c["economics"]["status"] == "ok"
        assert p["list_price_inr"] is not None and p["label"] and p["qualifying_orders_28d"] >= 0
        if p["kind"] == "cart":
            assert p["eligible_skus"] and p["min_cart_value_inr"] > 0
    # The product offer targets the declining weekday-afternoon slot found by analytics.
    prod = next(c for c in r["candidates"] if c["promotion"]["kind"] == "product")
    assert prod["promotion"]["hours"] == [14, 15, 16] and prod["promotion"]["weekdays_only"]
    assert r["explanations"][0]["source"] in ("llm", "template")
    # The stored campaigns are ordinary campaigns carrying the offer.
    camp = client.get(f"{C}/{prod['campaign_id']}").json()
    assert camp["campaign_type"] == "product_offer"
    assert camp["current"]["inputs"]["promotion"]["skus"] == prod["promotion"]["skus"]


def test_ai_cannot_use_products_the_merchant_did_not_select(client: TestClient) -> None:
    client.put(f"{M}/products/selection", json={"skus": ["CF001", "SW001"]})

    class Sneaky(MockProvider):
        def generate_promotions(self, ctx: PromotionContext) -> PromotionCandidateSet:
            assert {p.sku for p in ctx.products} == {"CF001", "SW001"}
            eo = ExtraOrders(conservative=1, expected=2, optimistic=3)
            return PromotionCandidateSet(
                candidates=[
                    PromotionCandidate(
                        kind="product",
                        skus=["BK001"],
                        discount_inr=20,
                        extra_orders=eo,
                        rationale="x",
                    ),
                    PromotionCandidate(
                        kind="product",
                        skus=["CF001"],
                        discount_inr=20,
                        extra_orders=eo,
                        rationale="y",
                    ),
                ]
            )

    app.dependency_overrides[provider_dependency] = lambda: Sneaky()
    try:
        r = run(client)
    finally:
        app.dependency_overrides.pop(provider_dependency)
    a, b = r["candidates"]
    assert a["status"] == "filtered_by_preference" and a["campaign_id"] is None
    assert "did not select" in a["filter_reasons"][0] and "Veg burger" in a["filter_reasons"][0]
    assert b["status"] == "evaluated"


def test_no_selected_products_is_refused(client: TestClient) -> None:
    client.put(f"{M}/products/selection", json={"skus": []})
    r = client.post(f"{M}/offers/recommendations", json={})
    assert r.status_code == 422 and r.json()["error_code"] == "no_products_selected"


def test_basket_check_applies_the_exclusion_rule(client: TestClient) -> None:
    r = run(client)
    ids = [c["campaign_id"] for c in r["candidates"]]
    skus = sorted({s for c in r["candidates"] for s in c["promotion"]["skus"]})
    lines = [{"sku": s, "quantity": 1} for s in skus] + [{"sku": "BK001", "quantity": 3}]
    check = client.post(f"{M}/baskets/check", json={"lines": lines, "campaign_ids": ids}).json()
    prices = {p["sku"]: p["price_inr"] for p in client.get(f"{M}/products").json()["products"]}
    assert check["cart_total_inr"] == sum(prices[s] for s in skus) + 3 * prices["BK001"]
    used = [u for u in check["units"] if u["used_by"] and not u["used_by"].startswith("Cart")]
    # Units used by a product or bundle offer are never cart-eligible.
    assert used and not any(u["cart_eligible"] for u in used)
    assert check["customer_pays_inr"] == check["cart_total_inr"] - check["total_discount_inr"]
    bad = client.post(f"{M}/baskets/check", json={"lines": [{"sku": "NOPE", "quantity": 1}]})
    assert bad.status_code == 422


def test_offer_edit_rebuilds_inputs_and_needs_new_approval(client: TestClient) -> None:
    r = run(client)
    prod = next(c for c in r["candidates"] if c["promotion"]["kind"] == "product")
    cid = prod["campaign_id"]
    e = client.post(f"{C}/{cid}/offer-versions", json={"discount_inr": 40, "change_note": "bigger"})
    assert e.status_code == 201, e.text
    v2 = e.json()
    p = v2["current"]["inputs"]["promotion"]
    assert v2["current_version"] == 2 and p["discount_inr"] == 40 and "₹40 off" in p["label"]
    assert v2["current"]["assumption_source"] == "merchant_edit"
    cap = next(
        x for x in v2["current"]["evaluation"]["policy"]["rules"] if x["rule_id"] == "discount_cap"
    )
    assert cap["passed"] is False  # ₹40 on a ₹180 cappuccino is above the 15% cap
    # Raw offers cannot be pushed through the generic campaign routes.
    raw = client.post(f"{C}/{cid}/versions", json={"campaign": v2["current"]["inputs"]})
    assert raw.status_code == 422 and raw.json()["detail"]["error_code"] == "use_offer_endpoints"


def test_approved_offer_runs_as_a_simulated_experiment(client: TestClient) -> None:
    r = run(client)
    chosen = r["selection"]["campaign_id"]
    assert chosen is not None
    camp = client.get(f"{C}/{chosen}").json()
    d = client.post(
        f"{C}/{chosen}/versions/1/decision",
        json={"decision": "approved", "input_hash": camp["current"]["input_hash"]},
    )
    assert d.status_code == 200, d.text
    x = client.post(
        f"{C}/{chosen}/versions/1/execute",
        headers={"Idempotency-Key": "offer-key-0001"},
        json={"simulation": {"max_customers": 400}},
    )
    assert x.status_code == 201, x.text
    exp = x.json()
    assert (
        exp["is_simulated"]
        and "this" in exp["assignment"]["rule"]
        and "offer" in exp["assignment"]["rule"]
    )


def test_execution_refused_if_product_deselected_after_approval(client: TestClient) -> None:
    r = run(client)
    prod = next(
        c
        for c in r["candidates"]
        if c["promotion"]["kind"] == "product" and c["policy"]["permitted"]
    )
    cid = prod["campaign_id"]
    camp = client.get(f"{C}/{cid}").json()
    client.post(
        f"{C}/{cid}/versions/1/decision",
        json={"decision": "approved", "input_hash": camp["current"]["input_hash"]},
    )
    others = [
        s
        for s in client.get(f"{M}/products").json()["selected_skus"]
        if s not in prod["promotion"]["skus"]
    ]
    client.put(f"{M}/products/selection", json={"skus": others})
    x = client.post(
        f"{C}/{cid}/versions/1/execute", headers={"Idempotency-Key": "offer-key-0002"}, json={}
    )
    assert x.status_code == 409 and x.json()["details"]["blocked_by"] == ["product_selection"]
