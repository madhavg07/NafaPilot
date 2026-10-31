"""Product analytics: per-product demand and margin, and which products sell together.

Co-purchase is a simple market-basket analysis over the last 28 days of order lines:
  support(A,B)     orders with both / all orders
  confidence(A→B)  orders with both / orders with A
  lift(A,B)        support(A,B) / (support(A) × support(B))
A lift above 1 means the pair appears together more often than chance. These are
observed associations in past orders; they do not show that one product causes the other.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import combinations

import pandas as pd

from app.data import DataRepository
from app.schemas.promotions import (
    BasketStatsOut,
    CatalogueOut,
    PairOut,
    ProductOut,
    ProductStats,
)
from app.services.campaign_store import CampaignError
from app.services.promotions.engine import WINDOW_DAYS, Product

MIN_PAIR_ORDERS = 10
NOTICE = (
    "Synthetic product catalogue and order lines. Co-purchase figures are associations in "
    "past orders, not proof that one product drives another."
)


class NoProductData(CampaignError):
    """The merchant has no product catalogue or order lines."""

    http_status, error_code = 422, "no_product_data"


def products_map(repo: DataRepository, merchant_id: str) -> dict[str, Product]:
    p = repo.products_for(merchant_id)
    return {
        str(r.sku): Product(
            sku=str(r.sku),
            name=str(r.name),
            price=Decimal(str(r.price_inr)),
            cost=None if pd.isna(r.unit_cost_inr) else Decimal(str(r.unit_cost_inr)),
        )
        for r in p.itertuples()
    }


def windows(repo: DataRepository) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    as_of = pd.Timestamp(repo.as_of)
    start = as_of - pd.Timedelta(days=WINDOW_DAYS - 1)
    prev_start = start - pd.Timedelta(days=WINDOW_DAYS)
    return prev_start, start, as_of


def _lines_in(
    orders: pd.DataFrame, items: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = pd.to_datetime(orders["order_date"])
    o = orders[(d >= start) & (d <= end)]
    return o, items[items["order_id"].isin(set(o["order_id"]))]


def co_purchase(
    lines: pd.DataFrame, n_orders: int, products: dict[str, Product], limit: int = 12
) -> list[PairOut]:
    if lines.empty or n_orders == 0:
        return []
    baskets = lines.groupby("order_id")["sku"].apply(lambda s: sorted(set(s)))
    single = lines.groupby("sku")["order_id"].nunique()
    together: dict[tuple[str, str], int] = {}
    for skus in baskets:
        for a, b in combinations(skus, 2):
            together[(a, b)] = together.get((a, b), 0) + 1
    pairs = []
    for (a, b), n_ab in together.items():
        if n_ab < MIN_PAIR_ORDERS or a not in products or b not in products:
            continue
        sa, sb = single[a] / n_orders, single[b] / n_orders
        lift = (n_ab / n_orders) / (sa * sb)
        # Report the direction with the higher confidence (A is the "trigger" product).
        conf_ab, conf_ba = n_ab / single[a], n_ab / single[b]
        x, y, conf = (a, b, conf_ab) if conf_ab >= conf_ba else (b, a, conf_ba)
        pairs.append(
            PairOut(
                sku_a=x,
                sku_b=y,
                name_a=products[x].name,
                name_b=products[y].name,
                orders_together_28d=int(n_ab),
                confidence_a_to_b=round(float(conf), 3),
                lift=round(float(lift), 2),
            )
        )
    pairs.sort(key=lambda p: (-p.lift * min(p.orders_together_28d, 100), p.sku_a, p.sku_b))
    return [p for p in pairs if p.lift > 1.0][:limit]


def catalogue_out(repo: DataRepository, merchant_id: str) -> CatalogueOut:
    products = products_map(repo, merchant_id)
    if not products:
        raise NoProductData(
            f"Merchant {merchant_id} has no product catalogue, so only merchant-level "
            "recommendations are possible."
        )
    orders, items = repo.orders_for(merchant_id), repo.items_for(merchant_id)
    prev_start, start, as_of = windows(repo)
    o_now, l_now = _lines_in(orders, items, start, as_of)
    _, l_prev = _lines_in(orders, items, prev_start, start - pd.Timedelta(days=1))
    n_orders = len(o_now)
    source, version, selected = repo.product_selection(merchant_id)
    chosen = set(selected)

    units_now = l_now.groupby("sku")["quantity"].sum()
    units_prev = l_prev.groupby("sku")["quantity"].sum()
    orders_now = l_now.groupby("sku")["order_id"].nunique()
    revenue = l_now.groupby("sku")["line_value_inr"].sum()
    cost = l_now.groupby("sku")["line_cost_inr"].sum(min_count=1)
    cost_missing = l_now.groupby("sku")["line_cost_inr"].apply(lambda s: bool(s.isna().any()))

    categories = repo.products_for(merchant_id).set_index("sku")["category"].to_dict()
    out = []
    for sku, p in products.items():
        u_now, u_prev = int(units_now.get(sku, 0)), int(units_prev.get(sku, 0))
        rev = Decimal(str(round(float(revenue.get(sku, 0)), 2)))
        contrib = (
            None
            if p.cost is None or bool(cost_missing.get(sku, False))
            else rev - Decimal(str(round(float(cost.get(sku, 0) or 0), 2)))
        )
        margin = None if p.cost is None else p.price - p.cost
        out.append(
            ProductOut(
                sku=sku,
                name=p.name,
                category=str(categories.get(sku, "")),
                price_inr=p.price,
                unit_cost_inr=p.cost,
                margin_inr=margin,
                margin_pct=None if margin is None else round(float(margin / p.price * 100), 1),
                selected=sku in chosen,
                stats=ProductStats(
                    units_28d=u_now,
                    orders_28d=int(orders_now.get(sku, 0)),
                    revenue_28d_inr=rev,
                    contribution_28d_inr=contrib,
                    units_prev_28d=u_prev,
                    trend_pct=None if u_prev == 0 else round((u_now - u_prev) / u_prev * 100, 1),
                    share_of_orders=0.0
                    if n_orders == 0
                    else round(int(orders_now.get(sku, 0)) / n_orders, 3),
                ),
            )
        )
    values = o_now["order_value_inr"] if n_orders else pd.Series([0.0])
    items_per = l_now.groupby("order_id")["quantity"].sum()
    return CatalogueOut(
        merchant_id=merchant_id,
        period_start=start.date().isoformat(),
        period_end=as_of.date().isoformat(),
        products=out,
        pairs=co_purchase(l_now, n_orders, products),
        basket=BasketStatsOut(
            orders_28d=n_orders,
            average_value_inr=Decimal(str(round(float(values.mean()), 2))),
            median_value_inr=Decimal(str(round(float(values.median()), 2))),
            p75_value_inr=Decimal(str(round(float(values.quantile(0.75)), 2))),
            average_items=round(float(items_per.mean()), 2) if len(items_per) else 0.0,
        ),
        selection_source="merchant" if source == "merchant" else "default",
        selection_version=version,
        selected_skus=selected,
        is_synthetic=repo.is_synthetic,
        data_source=repo.data_source,
        notice=NOTICE,
    )


def merchant_cost_ratio(repo: DataRepository, merchant_id: str) -> Decimal | None:
    o = repo.orders_for(merchant_id)
    known = o.dropna(subset=["variable_cost_inr"])
    if known.empty or known["order_value_inr"].sum() <= 0:
        return None
    return Decimal(str(round(known["variable_cost_inr"].sum() / known["order_value_inr"].sum(), 4)))
