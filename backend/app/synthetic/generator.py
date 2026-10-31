"""Seeded synthetic dataset generator.

generate(seed) returns every table as a pandas DataFrame; write_dataset() saves them as
CSV plus a manifest. The same seed always produces byte-identical files (no wall-clock
time is used anywhere).

All records are synthetic. Ground-truth campaign effects are stored so later milestones
can check that the analytics and experiment engines recover them. They are simulation
parameters, not evidence about real merchants.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from app.synthetic.specs import (
    AFTERNOON_HOURS,
    ALL_MERCHANTS,
    ANAND_AFTERNOON_WEEKLY,
    CAMPAIGN_PERIOD_START,
    CATALOGUES,
    DEFAULT_SEED,
    GENERATOR_VERSION,
    HOUR_PROFILES,
    MERCHANTS,
    N_WEEKS,
    PLANTED_CAMPAIGNS,
    RANDOM_CAMPAIGN_MERCHANTS,
    WINDOW_END,
    WINDOW_START,
    CampaignSpec,
    MerchantSpec,
    ProductSpec,
)

DISCLAIMER = (
    "Synthetic data generated for the NafaPilot hackathon prototype. Not real merchants, "
    "customers or transactions. Not Paytm data."
)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TABLES = (
    "merchants",
    "merchant_policies",
    "products",
    "customers",
    "orders",
    "order_items",
    "campaigns",
    "assignments",
    "outcomes",
    "feedback",
    "invalid_orders",
)


@dataclass
class Dataset:
    seed: int
    tables: dict[str, pd.DataFrame]

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]


# ----------------------------------------------------------------------------- helpers


def _zero_sum_jitter(rng: np.random.Generator, n: int, spread: int) -> npt.NDArray[np.int64]:
    """n integers in [-spread, spread] (roughly) that sum to exactly zero."""
    jitter = rng.integers(-spread, spread + 1, size=n)
    jitter[-1] -= int(jitter.sum())
    return jitter.astype(np.int64)


def _hourly_counts(rng: np.random.Generator, m: MerchantSpec) -> pd.DataFrame:
    """One row per (date, hour) with an order count."""
    profile = np.array(HOUR_PROFILES[m.category])
    first_day = (N_WEEKS - m.history_weeks) * 7
    rows: list[tuple[date, int, int]] = []
    anand = m.scenario_tag == "anand_afternoon_decline"
    afternoon_share = profile[list(AFTERNOON_HOURS)].sum()

    for week in range(N_WEEKS):
        weekday_afternoon: npt.NDArray[np.int64] | None = None
        if anand:
            target = ANAND_AFTERNOON_WEEKLY[week]
            weekday_afternoon = target + _zero_sum_jitter(rng, 5, 2)
        for dow in range(7):
            offset = week * 7 + dow
            if offset < first_day:
                continue
            if m.gap and m.gap[0] <= offset < m.gap[0] + m.gap[1]:
                continue
            day = WINDOW_START + timedelta(days=offset)
            factor = (1 + m.trend_per_week) ** week * (m.weekend_factor if dow >= 5 else 1.0)
            means = m.daily_orders * factor * profile
            counts = rng.poisson(means)
            if anand and dow < 5 and weekday_afternoon is not None:
                # Exact slot total for the chart; the rest of the day stays Poisson.
                slot_total = int(weekday_afternoon[dow])
                split = rng.multinomial(
                    slot_total, profile[list(AFTERNOON_HOURS)] / afternoon_share
                )
                counts[list(AFTERNOON_HOURS)] = split
            for hour in np.flatnonzero(counts):
                rows.append((day, int(hour), int(counts[hour])))
    return pd.DataFrame(rows, columns=["date", "hour", "count"])


def _basket(
    brng: np.random.Generator,
    cat: tuple[ProductSpec, ...],
    pop: npt.NDArray[np.float64],
    index: dict[str, int],
    target: float,
) -> dict[int, int]:
    """Build one basket towards a target value: a first item picked by popularity, its
    planted companions with their chances, then more items while that gets closer to the
    target. Returns {catalogue index: quantity}."""
    lines: dict[int, int] = {}
    total = 0

    def add(i: int) -> None:
        nonlocal total
        lines[i] = lines.get(i, 0) + 1
        total += cat[i].price_inr

    first = int(brng.choice(len(cat), p=pop))
    add(first)
    for sku, chance in cat[first].companions:
        if brng.random() < chance:
            add(index[sku])
    while total < target:
        i = int(brng.choice(len(cat), p=pop))
        if total + cat[i].price_inr - target > target - total:
            break
        add(i)
    return lines


def _baskets_for(
    m: MerchantSpec, orders: pd.DataFrame, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fill each order with products from the merchant's catalogue.

    Order value and variable cost become the sums of their lines, so orders.csv and
    order_items.csv always agree. Product costs are calibrated per merchant so the
    merchant's overall cost ratio matches its spec. A separate seeded stream is used, so
    adding products does not change any other draw.
    """
    cat = CATALOGUES[m.category]
    index = {p.sku: i for i, p in enumerate(cat)}
    pop = np.array([p.popularity for p in cat], dtype=float)
    pop /= pop.sum()
    targets = orders["target_inr"].to_numpy(dtype=float)

    def build(scale: float) -> list[dict[int, int]]:
        brng = np.random.default_rng([seed, int(m.merchant_id[1:]), 101])
        return [_basket(brng, cat, pop, index, t * scale) for t in targets]

    def mean_value(bs: list[dict[int, int]]) -> float:
        return sum(cat[i].price_inr * q for b in bs for i, q in b.items()) / max(len(bs), 1)

    # Baskets overshoot their targets a little (companions); one calibration pass brings
    # the merchant's average order value back to its spec.
    baskets = build(1.0)
    baskets = build(m.aov / mean_value(baskets))

    qty = np.zeros(len(cat))
    for b in baskets:
        for i, q in b.items():
            qty[i] += q
    value = float(sum(qty[i] * p.price_inr for i, p in enumerate(cat)))
    raw_cost = float(sum(qty[i] * p.unit_cost_inr for i, p in enumerate(cat)))
    if m.vc_per_order is None:
        unit_cost = [float("nan")] * len(cat)
    else:
        factor = (m.vc_per_order / m.aov) * value / raw_cost
        unit_cost = [float(round(p.unit_cost_inr * factor)) for p in cat]

    products = pd.DataFrame(
        {
            "merchant_id": m.merchant_id,
            "sku": [p.sku for p in cat],
            "name": [p.name for p in cat],
            "category": [p.category for p in cat],
            "price_inr": [p.price_inr for p in cat],
            "unit_cost_inr": unit_cost,
            "is_synthetic": True,
        }
    )

    item_rows: list[tuple[str, str, str, int, int, float, int, float]] = []
    values, costs = [], []
    for oid, b, no_cost in zip(orders["order_id"], baskets, orders["missing_cost"], strict=True):
        v, c = 0, 0.0
        for i in sorted(b):
            p, q = cat[i], b[i]
            uc = float("nan") if no_cost else unit_cost[i]
            item_rows.append(
                (oid, m.merchant_id, p.sku, q, p.price_inr, uc, q * p.price_inr, q * uc)
            )
            v += q * p.price_inr
            c += q * uc
        values.append(v)
        costs.append(c)
    items = pd.DataFrame(
        item_rows,
        columns=[
            "order_id",
            "merchant_id",
            "sku",
            "quantity",
            "unit_price_inr",
            "unit_cost_inr",
            "line_value_inr",
            "line_cost_inr",
        ],
    )
    items["is_synthetic"] = True
    orders = orders.assign(order_value_inr=values, variable_cost_inr=costs)
    orders["order_value_inr"] = orders["order_value_inr"].astype(np.int64)
    return orders, items, products


def _orders_for(rng: np.random.Generator, m: MerchantSpec, customer_ids: list[str]) -> pd.DataFrame:
    slots = _hourly_counts(rng, m)
    n = int(slots["count"].sum())
    dates = np.repeat(slots["date"].to_numpy(), slots["count"].to_numpy())
    hours = np.repeat(slots["hour"].to_numpy(), slots["count"].to_numpy())
    minutes = rng.integers(0, 60, size=n)
    seconds = rng.integers(0, 60, size=n)
    ts = [
        datetime(d.year, d.month, d.day, int(h), int(mi), int(s))
        for d, h, mi, s in zip(dates, hours, minutes, seconds, strict=True)
    ]

    # Drawn from the main stream (keeps every later draw stable), then used as the
    # target value each basket is built towards.
    targets = np.clip(rng.normal(m.aov, 0.2 * m.aov, size=n), 0.3 * m.aov, None).round()
    missing_cost = (
        rng.random(n) < m.missing_cost_share if m.missing_cost_share > 0 else np.zeros(n, bool)
    )

    # Frequent customers order more often (Zipf-like weights); some orders are walk-ins.
    weights = 1.0 / np.arange(1, len(customer_ids) + 1) ** 0.8
    weights /= weights.sum()
    picks = rng.choice(len(customer_ids), size=n, p=weights)
    walk_in = rng.random(n) < m.walk_in_share
    cust = np.array(customer_ids, dtype=object)[picks]
    cust[walk_in] = None

    df = pd.DataFrame(
        {
            "merchant_id": m.merchant_id,
            "customer_id": cust,
            "order_ts": ts,
            "target_inr": targets,
            "missing_cost": missing_cost,
        }
    ).sort_values("order_ts", kind="stable", ignore_index=True)
    df.insert(0, "order_id", [f"{m.merchant_id}-O{i:06d}" for i in range(1, len(df) + 1)])
    df["order_date"] = df["order_ts"].dt.date.astype(str)
    df["weekday"] = df["order_ts"].dt.day_of_week.map(dict(enumerate(WEEKDAYS)))
    df["hour"] = df["order_ts"].dt.hour
    df["discount_inr"] = 0
    # Repeat = this customer ordered here earlier in the window. Unknown for walk-ins.
    known = df["customer_id"].notna()
    is_repeat = pd.Series(pd.NA, index=df.index, dtype="boolean")
    is_repeat[known] = df[known].groupby("customer_id").cumcount() > 0
    df["is_repeat"] = is_repeat
    df["order_ts"] = df["order_ts"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    df["is_synthetic"] = True
    return df


ORDER_COLUMNS = [
    "order_id",
    "merchant_id",
    "customer_id",
    "order_ts",
    "order_date",
    "weekday",
    "hour",
    "order_value_inr",
    "variable_cost_inr",
    "discount_inr",
    "is_repeat",
    "is_synthetic",
]


def _segment(order_count: int) -> str:
    if order_count >= 8:
        return "regular"
    if order_count >= 2:
        return "occasional"
    if order_count == 1:
        return "one_time"
    return "inactive"


# --------------------------------------------------------------------------- campaigns


def expected_profit(c: CampaignSpec, m: MerchantSpec) -> float | None:
    """Ground-truth expected incremental contribution profit, using the project's
    accounting convention (discount counted once, inside net revenue):

        I = n_treated × lift                     incremental orders
        S = n_treated × base_rate × redeem_share discounted orders that would have happened
        ΔP = I × (AOV − d − VC) − S × d − C
    """
    if m.vc_per_order is None:
        return None
    n_treated = c.eligible - round(c.eligible * m.holdout_pct / 100)
    incremental = n_treated * c.lift
    subsidised = n_treated * c.base_rate * c.redeem_share_base
    unit_margin = m.aov - c.discount_inr - m.vc_per_order
    return incremental * unit_margin - subsidised * c.discount_inr - c.campaign_cost_inr


def outcome_label(c: CampaignSpec, m: MerchantSpec) -> str:
    profit = expected_profit(c, m)
    if c.discount_inr / m.aov * 100 > m.discount_cap_pct:
        return "discount_cap_breach"
    if c.lift == 0:
        return "null_effect"
    if profit is None:
        return "unknown_cost"
    if profit < -m.max_loss_inr:
        return "loss_limit_violation"
    if profit < 0:
        return "unprofitable"
    return "profitable"


def _random_campaign(
    rng: np.random.Generator, m: MerchantSpec, number: int, existing: int
) -> CampaignSpec:
    kind = str(rng.choice(["targeted_offer", "off_peak", "new_customer"]))
    target = {
        "targeted_offer": "eligible_existing_customers",
        "off_peak": "all_walk_ins",
        "new_customer": "new_customers",
    }[kind]
    discount = int(round(m.aov * rng.uniform(0.05, 0.15)))
    return CampaignSpec(
        campaign_id=f"C{number:03d}",
        merchant_id=m.merchant_id,
        campaign_type=kind,
        discount_inr=max(discount, 5),
        target_group=target,
        campaign_cost_inr=int(rng.integers(5, 21)) * 100,
        eligible=int(rng.integers(150, min(m.customer_pool, 600) + 1)),
        base_rate=round(float(rng.uniform(0.2, 0.5)), 3),
        lift=round(float(rng.uniform(0.05, 0.35)), 3),
        redeem_share_base=round(float(rng.uniform(0.2, 0.5)), 3),
        start_offset_days=35 + 7 * existing,
        note="random",
    )


def _campaign_records(
    rng: np.random.Generator,
    specs: list[CampaignSpec],
    merchants: dict[str, MerchantSpec],
    pools: dict[str, list[str]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    campaigns, assignments, outcomes = [], [], []
    for c in specs:
        m = merchants[c.merchant_id]
        start = CAMPAIGN_PERIOD_START + timedelta(days=c.start_offset_days)
        profit = expected_profit(c, m)
        campaigns.append(
            {
                "campaign_id": c.campaign_id,
                "merchant_id": c.merchant_id,
                "campaign_type": c.campaign_type,
                "discount_inr": c.discount_inr,
                "target_group": c.target_group,
                "campaign_cost_inr": c.campaign_cost_inr,
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=c.duration_days - 1)).isoformat(),
                "eligible_customers": c.eligible,
                "holdout_pct": m.holdout_pct,
                "status": "executed",
                "execution_mode": "simulated",
                "true_base_rate": c.base_rate,
                "true_lift_per_customer": c.lift,
                "true_redeem_share_base": round(c.redeem_share_base, 4),
                "true_expected_incremental_profit_inr": (
                    None if profit is None else round(profit, 2)
                ),
                "outcome_label": outcome_label(c, m),
                "note": c.note,
                "is_synthetic": True,
            }
        )
        eligible = rng.choice(np.array(pools[c.merchant_id]), size=c.eligible, replace=False)
        order = rng.permutation(c.eligible)
        n_hold = round(c.eligible * m.holdout_pct / 100)
        groups = np.where(np.isin(np.arange(c.eligible), order[:n_hold]), "holdout", "treatment")
        treated = groups == "treatment"
        base = rng.poisson(c.base_rate, size=c.eligible)
        extra = np.where(treated, rng.poisson(c.lift, size=c.eligible), 0)
        base_redeemed = np.where(treated, rng.binomial(base, c.redeem_share_base), 0)
        orders = base + extra
        redemptions = extra + base_redeemed
        for i in range(c.eligible):
            assignments.append(
                {"campaign_id": c.campaign_id, "customer_id": eligible[i], "group": groups[i]}
            )
            outcomes.append(
                {
                    "campaign_id": c.campaign_id,
                    "customer_id": eligible[i],
                    "group": groups[i],
                    "orders": int(orders[i]),
                    "redemptions": int(redemptions[i]),
                    "gross_revenue_inr": int(orders[i]) * m.aov,
                    "discount_given_inr": int(redemptions[i]) * c.discount_inr,
                    "is_simulated": True,
                }
            )
    return pd.DataFrame(campaigns), pd.DataFrame(assignments), pd.DataFrame(outcomes)


def _feedback(campaigns: pd.DataFrame, merchant_ids: set[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for c in campaigns.itertuples():
        rows.append(
            {
                "merchant_id": c.merchant_id,
                "campaign_id": c.campaign_id,
                "campaign_type": c.campaign_type,
                "discount_inr": c.discount_inr,
                "decision": "approved",
                "reason": None,
                "preference_key": None,
                "preference_value": None,
                "recorded_at": (date.fromisoformat(c.start_date) - timedelta(days=1)).isoformat(),
            }
        )
    proposals: list[tuple[str, str, int, str, str | None, str | None, str | None, str]] = [
        # The deck's scenario: merchant rejects discounting existing customers.
        (
            "M001",
            "off_peak",
            30,
            "rejected",
            "I don't want to give discounts to existing customers. I want new customers.",
            "target",
            "new_customers",
            "2026-10-20",
        ),
        # Rejection without a reason: record it, learn nothing about why.
        ("M003", "off_peak", 35, "rejected", None, None, None, "2026-10-12"),
        # Edit: merchant accepts the idea but lowers the discount.
        (
            "M004",
            "targeted_offer",
            70,
            "edited",
            "Keep the offer but at ₹40 off.",
            "max_discount_inr",
            "40",
            "2026-10-15",
        ),
        # Strategic rejection: valid reason, but no lasting preference to store.
        (
            "M002",
            "targeted_offer",
            12,
            "rejected",
            "Festival rush next week, no offers needed now.",
            None,
            None,
            "2026-10-18",
        ),
        ("M003", "new_customer", 25, "approved", None, None, None, "2026-10-21"),
    ]
    for mid, ctype, disc, decision, reason, pkey, pval, when in proposals:
        if mid not in merchant_ids:
            continue
        rows.append(
            {
                "merchant_id": mid,
                "campaign_id": None,
                "campaign_type": ctype,
                "discount_inr": disc,
                "decision": decision,
                "reason": reason,
                "preference_key": pkey,
                "preference_value": pval,
                "recorded_at": when,
            }
        )
    df = pd.DataFrame(rows).sort_values(["recorded_at", "merchant_id"], ignore_index=True)
    df.insert(0, "feedback_id", [f"F{i:04d}" for i in range(1, len(df) + 1)])
    df["is_synthetic"] = True
    return df


def _invalid_orders() -> pd.DataFrame:
    """Hand-written broken rows for testing validation. Never mixed into orders.csv."""
    base: dict[str, Any] = {
        "merchant_id": "M001",
        "customer_id": "M001-C0001",
        "order_ts": "2026-10-05T14:10:00",
        "order_date": "2026-10-05",
        "weekday": "Mon",
        "hour": 14,
        "order_value_inr": 200,
        "variable_cost_inr": 110.0,
        "discount_inr": 0,
        "is_repeat": True,
        "is_synthetic": True,
    }
    defects: list[tuple[str, dict[str, Any]]] = [
        ("negative_order_value", {"order_value_inr": -200}),
        ("hour_out_of_range", {"hour": 25}),
        ("missing_merchant_id", {"merchant_id": None}),
        ("unparseable_timestamp", {"order_ts": "2026-13-45T99:00:00", "order_date": "2026-13-45"}),
        ("unknown_merchant", {"merchant_id": "M999"}),
        ("cost_exceeds_value", {"variable_cost_inr": 350.0}),
        ("missing_order_value", {"order_value_inr": None}),
        ("duplicate_order_id", {}),
        ("duplicate_order_id", {}),
    ]
    rows = []
    for i, (issue, change) in enumerate(defects, start=1):
        order_id = "BAD-DUP" if issue == "duplicate_order_id" else f"BAD-{i:03d}"
        rows.append({"order_id": order_id, **base, **change, "expected_issue": issue})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------ public


def generate(seed: int = DEFAULT_SEED, *, include_test_merchants: bool = False) -> Dataset:
    """The shipped dataset has the four shops in MERCHANTS. Tests also add the edge-case
    merchants in TEST_MERCHANTS; the four shops' orders are identical either way."""
    rng = np.random.default_rng(seed)
    specs_used = ALL_MERCHANTS if include_test_merchants else MERCHANTS
    merchants = {m.merchant_id: m for m in specs_used}

    merchant_rows, policy_rows, customer_frames, order_frames = [], [], [], []
    product_frames, item_frames = [], []
    pools: dict[str, list[str]] = {}
    for m in specs_used:
        merchant_rows.append(
            {
                "merchant_id": m.merchant_id,
                "name": m.name,
                "category": m.category,
                "city": m.city,
                "average_order_value_inr": m.aov,
                "variable_cost_per_order_inr": m.vc_per_order,
                "scenario_tag": m.scenario_tag,
                "history_start": (
                    WINDOW_START + timedelta(days=(N_WEEKS - m.history_weeks) * 7)
                ).isoformat(),
                "is_synthetic": True,
            }
        )
        policy_rows.append(
            {
                "merchant_id": m.merchant_id,
                "policy_version": 1,
                "discount_cap_pct": m.discount_cap_pct,
                "min_margin_pct": m.min_margin_pct,
                "max_loss_inr": m.max_loss_inr,
                "budget_inr": m.budget_inr,
                "holdout_pct": m.holdout_pct,
                "risk_scenario": "conservative",
            }
        )
        pool = [f"{m.merchant_id}-C{i:04d}" for i in range(1, m.customer_pool + 1)]
        pools[m.merchant_id] = pool
        orders, items, products = _baskets_for(m, _orders_for(rng, m, pool), seed)
        orders = orders[ORDER_COLUMNS]
        order_frames.append(orders)
        item_frames.append(items)
        product_frames.append(products)

        stats = orders.dropna(subset=["customer_id"]).groupby("customer_id")
        counts = stats.size()
        first = stats["order_date"].min()
        customer_frames.append(
            pd.DataFrame(
                {
                    "customer_id": pool,
                    "merchant_id": m.merchant_id,
                    "first_order_date": [first.get(c) for c in pool],
                    "orders_in_window": [int(counts.get(c, 0)) for c in pool],
                }
            ).assign(segment=lambda d: d["orders_in_window"].map(_segment), is_synthetic=True)
        )

    specs = [c for c in PLANTED_CAMPAIGNS if c.merchant_id in merchants]
    per_merchant = {mid: sum(c.merchant_id == mid for c in specs) for mid in merchants}
    for mid in (x for x in RANDOM_CAMPAIGN_MERCHANTS if x in merchants):
        specs.append(_random_campaign(rng, merchants[mid], len(specs) + 1, per_merchant[mid]))
        per_merchant[mid] += 1
    campaigns, assignments, outcomes = _campaign_records(rng, specs, merchants, pools)

    tables = {
        "merchants": pd.DataFrame(merchant_rows),
        "merchant_policies": pd.DataFrame(policy_rows),
        "products": pd.concat(product_frames, ignore_index=True),
        "customers": pd.concat(customer_frames, ignore_index=True),
        "orders": pd.concat(order_frames, ignore_index=True),
        "order_items": pd.concat(item_frames, ignore_index=True),
        "campaigns": campaigns,
        "assignments": assignments,
        "outcomes": outcomes,
        "feedback": _feedback(campaigns, set(merchants)),
        "invalid_orders": _invalid_orders(),
    }
    return Dataset(seed=seed, tables=tables)


def write_dataset(ds: Dataset, out_dir: Path) -> dict[str, Any]:
    """Write CSVs (LF line endings on every OS) and manifest.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    for name in TABLES:
        path = out_dir / f"{name}.csv"
        ds[name].to_csv(path, index=False, lineterminator="\n")
        files[path.name] = {
            "rows": len(ds[name]),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": ds.seed,
        "order_window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "campaign_period_start": CAMPAIGN_PERIOD_START.isoformat(),
        "is_synthetic": True,
        "disclaimer": DISCLAIMER,
        "files": files,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest
