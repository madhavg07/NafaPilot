"""Eligibility and reproducible treatment/holdout assignment. Pure functions.

Seed: SHA-256 of "nafapilot-experiment:{campaign_version_id}:{input_hash}". The same
approved version always gets the same seed, and so the same assignment.

Assignment (exact split, not coin flips):
1. If a cap is given, the population is the `max_customers` eligible customers with the
   smallest SHA-256("{seed}:population:{customer_id}").
2. The population is ordered by SHA-256("{seed}:split:{customer_id}"); the first
   round_half_up(n × holdout_pct / 100) are holdout, the rest treatment. 300 at 20%
   gives exactly 240 / 60.
Both hashes depend only on the seed and the customer id, so group membership cannot
change on retry and does not depend on customer attributes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

ELIGIBILITY_RULES: dict[str, str] = {
    "targeted_offer": "Identified customers with at least one recorded order in the window.",
    "off_peak": "Identified customers with at least one recorded order in the window. "
    "Anonymous walk-in orders have no customer id and cannot be assigned.",
    "new_customer": "Customers on the merchant's (synthetic) customer list with no recorded "
    "order in the window.",
    # Product-level offers are tested one offer at a time on identified existing customers,
    # so a customer is never in two offers' experiments at once.
    "product_offer": "Identified customers with at least one recorded order in the window. "
    "Treatment customers get this product offer only; the holdout gets no offer.",
    "bundle_offer": "Identified customers with at least one recorded order in the window. "
    "Treatment customers get this bundle offer only; the holdout gets no offer.",
    "cart_offer": "Identified customers with at least one recorded order in the window. "
    "Treatment customers get this cart offer only; the holdout gets no offer.",
}


@dataclass
class Eligibility:
    rule: str
    customer_ids: list[str]
    excluded: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AssignedCustomer:
    customer_id: str
    group: str  # "treatment" | "holdout"
    assignment_hash: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def experiment_seed(campaign_version_id: str, input_hash: str) -> str:
    return _sha(f"nafapilot-experiment:{campaign_version_id}:{input_hash}")


def eligible_customers(
    customers: pd.DataFrame, campaign_type: str, anonymous_recent_orders: int
) -> Eligibility | None:
    """None if the campaign type has no eligibility rule. Problem records are counted
    in `excluded`, never dropped silently and never given invented identifiers."""
    rule = ELIGIBILITY_RULES.get(campaign_type)
    if rule is None:
        return None
    excluded: dict[str, int] = {}
    if anonymous_recent_orders:
        excluded["anonymous_orders_without_customer_id_last_28_days"] = anonymous_recent_orders
    if customers.empty:
        return Eligibility(rule, [], excluded)

    ids = customers["customer_id"].astype("string").str.strip()
    no_id = ids.isna() | (ids == "")
    if no_id.any():
        excluded["customer_records_without_id"] = int(no_id.sum())
    dup = ids.duplicated(keep=False) & ~no_id
    if dup.any():
        excluded["duplicate_customer_ids_ambiguous"] = int(dup.sum())
    history = pd.to_numeric(customers.get("orders_in_window"), errors="coerce")
    no_history = history.isna() & ~no_id & ~dup
    if no_history.any():
        excluded["customer_records_without_order_history"] = int(no_history.sum())

    usable = ~no_id & ~dup & ~no_history
    if campaign_type == "new_customer":
        keep = usable & (history == 0)
    else:
        keep = usable & (history >= 1)
    return Eligibility(rule, sorted(ids[keep].tolist()), excluded)


def holdout_size(n: int, holdout_pct: int) -> int:
    return int((Decimal(n) * holdout_pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def assign(
    customer_ids: list[str], seed: str, holdout_pct: int, max_customers: int | None = None
) -> list[AssignedCustomer]:
    population = sorted(set(customer_ids))
    if max_customers is not None and len(population) > max_customers:
        population = sorted(population, key=lambda c: _sha(f"{seed}:population:{c}"))
        population = population[:max_customers]
    ordered = sorted((_sha(f"{seed}:split:{c}"), c) for c in population)
    n_hold = holdout_size(len(ordered), holdout_pct)
    return [
        AssignedCustomer(c, "holdout" if i < n_hold else "treatment", h)
        for i, (h, c) in enumerate(ordered)
    ]
