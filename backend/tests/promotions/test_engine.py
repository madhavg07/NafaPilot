"""Promotion engine: deterministic allocation and history-based inputs."""

from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from app.data import DataRepository
from app.schemas.promotions import PromotionSpec
from app.services.decision.policy import evaluate_policy
from app.services.decision.profit import evaluate_campaign, input_hash
from app.services.promotions.catalogue import catalogue_out, products_map
from app.services.promotions.engine import Product, allocate, basket_check, build_input
from app.synthetic import generate

D = Decimal
PRODUCTS = {
    "CF001": Product("CF001", "Cappuccino", D(180), D(80)),
    "SW001": Product("SW001", "Veg sandwich", D(150), D(100)),
    "CD001": Product("CD001", "Cold drink", D(60), D(25)),
    "CH001": Product("CH001", "Potato chips", D(40), D(18)),
    "BK001": Product("BK001", "Burger", D(220), D(130)),
}
ALL = list(PRODUCTS)


def product(sku: str, d: int) -> PromotionSpec:
    return PromotionSpec(kind="product", skus=[sku], discount_inr=D(d))


def bundle(skus: list[str], d: int) -> PromotionSpec:
    return PromotionSpec(kind="bundle", skus=skus, discount_inr=D(d))


def cart(threshold: int, d: int, allowed: list[str] | None = None) -> PromotionSpec:
    return PromotionSpec(
        kind="cart",
        discount_inr=D(d),
        min_cart_value_inr=D(threshold),
        eligible_skus=allowed if allowed is not None else ALL,
    )


@pytest.fixture(scope="module")
def repo() -> DataRepository:
    return DataRepository.from_tables(generate(42).tables)


# --------------------------------------------------------------- allocation rules


def test_design_example_cart_offer_does_not_apply() -> None:
    """The ₹430 cart from the design note: only ₹100 is cart-eligible, so no ₹30 off."""
    basket = [("CF001", 1), ("SW001", 1), ("CD001", 1), ("CH001", 1)]
    out = basket_check(
        basket,
        [
            (None, product("CF001", 20)),
            (None, bundle(["CF001", "SW001"], 25)),
            (None, cart(300, 30)),
        ],
        PRODUCTS,
        ALL,
    )
    applied = {o.kind: o.applied for o in out.offers}
    assert applied == {"product": True, "bundle": False, "cart": False}
    assert out.cart_total_inr == 430 and out.total_discount_inr == 20
    # The bundle could not use the cappuccino already discounted, so the sandwich stays
    # cart-eligible: 150 + 60 + 40 = 250, still below 300.
    assert out.cart_eligible_subtotal_inr == 250
    assert out.contribution_before_inr == 430 - 223
    assert out.contribution_after_inr == 430 - 20 - 223


def test_bundle_units_are_excluded_from_the_cart() -> None:
    basket = [("CF001", 1), ("SW001", 1), ("CD001", 1), ("CH001", 1)]
    out = basket_check(
        basket, [(None, bundle(["CF001", "SW001"], 25)), (None, cart(300, 30))], PRODUCTS, ALL
    )
    assert [o.applied for o in out.offers] == [True, False]
    assert out.cart_eligible_subtotal_inr == 100  # only cold drink + chips


def test_cart_applies_when_eligible_items_reach_threshold() -> None:
    basket = [("BK001", 1), ("SW001", 1), ("CF001", 1)]
    out = basket_check(basket, [(None, product("CF001", 20)), (None, cart(300, 30))], PRODUCTS, ALL)
    assert [o.applied for o in out.offers] == [True, True]
    assert out.cart_eligible_subtotal_inr == 370 and out.total_discount_inr == 50


def test_second_unit_of_a_discounted_product_stays_cart_eligible() -> None:
    out = basket_check(
        [("CF001", 2)], [(None, product("CF001", 20)), (None, cart(150, 15))], PRODUCTS, ALL
    )
    used = [u.used_by for u in out.units]
    assert used[0] is not None and "Cappuccino" in used[0]
    assert out.offers[1].applied and out.cart_eligible_subtotal_inr == 180
    assert out.total_discount_inr == 35


def test_unselected_products_never_count_towards_a_cart_offer() -> None:
    out = basket_check(
        [("BK001", 2)], [(None, cart(300, 30, allowed=["CF001"]))], PRODUCTS, ["CF001"]
    )
    assert not out.offers[0].applied and out.cart_eligible_subtotal_inr == 0


def test_allocation_order_does_not_depend_on_input_order() -> None:
    offers = [
        ("a", cart(100, 10)),
        ("b", bundle(["CF001", "SW001"], 25)),
        ("c", product("CF001", 20)),
    ]
    one = allocate([("SW001", 1), ("CF001", 1)], offers, PRODUCTS)
    two = allocate([("CF001", 1), ("SW001", 1)], offers, PRODUCTS)
    assert one.discounts == two.discounts == {"c": D(20), "b": D(0), "a": D(10)}


def test_offer_shapes_are_validated() -> None:
    with pytest.raises(ValidationError):
        PromotionSpec(kind="product", skus=["A", "B"], discount_inr=D(5))
    with pytest.raises(ValidationError):
        PromotionSpec(kind="bundle", skus=["A", "A"], discount_inr=D(5))
    with pytest.raises(ValidationError):
        PromotionSpec(kind="cart", discount_inr=D(5))
    with pytest.raises(ValidationError):
        PromotionSpec(kind="product", skus=["A"], discount_inr=D(5), hours=[25])


# ------------------------------------------------------------- inputs from history


def test_inputs_come_from_qualifying_baskets(repo: DataRepository) -> None:
    pm = products_map(repo, "M001")
    spec = bundle(["CF001", "SW001"], 25)
    inp, notes = build_input(
        spec,
        campaign_ref="t",
        products=pm,
        orders=repo.orders_for("M001"),
        items=repo.items_for("M001"),
        as_of=pd.Timestamp(repo.as_of),
        incremental={"conservative": 10, "expected": 30, "optimistic": 60},
        campaign_cost_inr=D(600),
        merchant_cost_ratio=None,
    )
    p = inp.promotion
    assert (
        p is not None
        and p.list_price_inr == 330
        and p.item_cost_inr == pm["CF001"].cost + pm["SW001"].cost
    )
    assert p.qualifying_orders_28d > 50 and notes == []
    # Whole baskets that contain both products are worth more than the two products alone.
    assert inp.average_order_value_inr is not None and inp.average_order_value_inr >= 330
    s = inp.scenarios
    assert s["conservative"].existing_discounted_orders == p.qualifying_orders_28d
    assert s["expected"].existing_discounted_orders == (
        D(p.qualifying_orders_28d) * D("0.8")
    ).quantize(D("0.1"))
    econ = evaluate_campaign(inp)
    assert econ.status == "ok" and inp.campaign_type == "bundle_offer"


def test_ai_extra_orders_are_capped(repo: DataRepository) -> None:
    pm = products_map(repo, "M001")
    inp, notes = build_input(
        product("CF005", 9),
        campaign_ref="t",
        products=pm,
        orders=repo.orders_for("M001"),
        items=repo.items_for("M001"),
        as_of=pd.Timestamp(repo.as_of),
        incremental={"conservative": 10, "expected": 50_000, "optimistic": 90_000},
        campaign_cost_inr=D(500),
        merchant_cost_ratio=None,
    )
    assert inp.promotion is not None
    cap = 2 * inp.promotion.qualifying_orders_28d + 10
    assert inp.scenarios["optimistic"].incremental_orders == cap
    assert any("capped" in n for n in notes)


def test_policy_judges_product_offers_on_the_product_price(repo: DataRepository) -> None:
    pm = products_map(repo, "M001")
    policy = repo.merchant_policy("M001")
    assert policy is not None
    common = dict(
        campaign_ref="t",
        products=pm,
        orders=repo.orders_for("M001"),
        items=repo.items_for("M001"),
        as_of=pd.Timestamp(repo.as_of),
        incremental={"conservative": 5, "expected": 10, "optimistic": 20},
        campaign_cost_inr=D(500),
        merchant_cost_ratio=None,
    )
    ok, _ = build_input(product("CF001", 20), **common)  # 11.1% of ₹180
    big, _ = build_input(product("CF001", 40), **common)  # 22.2% > 15% cap
    for inp, passes in ((ok, True), (big, False)):
        rule = next(
            r
            for r in evaluate_policy(evaluate_campaign(inp), inp, policy).rules
            if r.rule_id == "discount_cap"
        )
        assert rule.passed is passes and "price of Cappuccino" in rule.reason
    assert input_hash(ok) != input_hash(big)


def test_catalogue_finds_planted_pairs(repo: DataRepository) -> None:
    c = catalogue_out(repo, "M001")
    assert 15 <= len(c.products) <= 20 and c.selection_source == "default"
    pairs = {(p.sku_a, p.sku_b) for p in c.pairs} | {(p.sku_b, p.sku_a) for p in c.pairs}
    assert ("CF001", "SW001") in pairs and ("CH001", "CD001") in pairs
    assert all(p.lift > 1 for p in c.pairs)
