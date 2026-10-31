"""Eligibility, reproducible 80/20 assignment and deterministic simulation (pure, no DB)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.experiments.assignment import (
    assign,
    eligible_customers,
    experiment_seed,
    holdout_size,
)
from app.services.experiments.simulation import SimulationParams, simulate

SEED = experiment_seed("version-1", "a" * 64)
IDS = [f"M001-C{i:05d}" for i in range(300)]


def test_seed_is_deterministic_and_version_specific() -> None:
    assert SEED == experiment_seed("version-1", "a" * 64)
    assert SEED != experiment_seed("version-2", "a" * 64)
    assert SEED != experiment_seed("version-1", "b" * 64)
    assert len(SEED) == 64


@pytest.mark.parametrize(
    ("n", "pct", "hold"), [(300, 20, 60), (1109, 20, 222), (5, 20, 1), (7, 50, 4), (0, 20, 0)]
)
def test_holdout_size_rounds_half_up(n: int, pct: int, hold: int) -> None:
    assert holdout_size(n, pct) == hold


def test_exact_80_20_split_and_reproducible() -> None:
    a = assign(IDS, SEED, 20)
    groups = [x.group for x in a]
    assert groups.count("treatment") == 240 and groups.count("holdout") == 60
    assert a == assign(list(reversed(IDS)), SEED, 20)  # input order does not matter
    assert {x.customer_id for x in a} == set(IDS)  # nobody dropped, nobody invented
    other = assign(IDS, experiment_seed("version-2", "a" * 64), 20)
    assert {x.customer_id for x in other if x.group == "holdout"} != {
        x.customer_id for x in a if x.group == "holdout"
    }


def test_group_membership_is_stable_when_population_grows() -> None:
    """A customer's hash never changes; only the cut-off moves."""
    a = {x.customer_id: x.assignment_hash for x in assign(IDS, SEED, 20)}
    b = {x.customer_id: x.assignment_hash for x in assign(IDS + ["M001-C99999"], SEED, 20)}
    assert all(b[c] == h for c, h in a.items())


def test_cap_selects_by_hash_deterministically() -> None:
    a = assign(IDS, SEED, 20, max_customers=100)
    assert len(a) == 100 and sum(x.group == "holdout" for x in a) == 20
    assert a == assign(IDS, SEED, 20, max_customers=100)


def test_eligibility_counts_problem_records_and_never_invents_ids() -> None:
    df = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C2", None, " ", "C3", "C4", "C5"],
            "orders_in_window": [3, 1, 2, 4, 1, None, 0, 1],
        }
    )
    e = eligible_customers(df, "targeted_offer", anonymous_recent_orders=12)
    assert e is not None
    assert e.customer_ids == ["C1", "C5"]
    assert e.excluded == {
        "anonymous_orders_without_customer_id_last_28_days": 12,
        "customer_records_without_id": 2,
        "duplicate_customer_ids_ambiguous": 2,
        "customer_records_without_order_history": 1,
    }
    new = eligible_customers(df, "new_customer", 0)
    assert new is not None and new.customer_ids == ["C4"]
    assert eligible_customers(df, "loyalty_points", 0) is None
    empty = eligible_customers(pd.DataFrame(), "off_peak", 3)
    assert empty is not None and empty.customer_ids == []


def _sim(lift: float, missing: float = 0.0, ids: list[str] = IDS) -> list:  # type: ignore[type-arg]
    return simulate(assign(ids, SEED, 20), SimulationParams(0.4, lift, 0.2, missing), SEED)


def test_simulation_is_deterministic_and_holdout_never_treated() -> None:
    a, b = _sim(0.3), _sim(0.3)
    assert a == b
    assert all(o.redemptions == 0 for o in a if o.group == "holdout")
    assert all(o.redemptions is not None and o.orders is not None for o in a)
    assert all(0 <= o.redemptions <= o.orders for o in a)  # type: ignore[operator]


def test_negative_lift_lowers_orders_and_missing_outcomes_are_null() -> None:
    low = _sim(-0.3)
    assert sum(o.orders for o in low if o.group == "treatment") < sum(  # type: ignore[misc]
        o.orders for o in _sim(0.0) if o.group == "treatment"
    )
    miss = _sim(0.3, missing=0.5)
    nulls = [o for o in miss if o.orders is None]
    assert 100 < len(nulls) < 200
    assert all(o.redemptions is None for o in nulls)
    assert len(miss) == 300  # still one record per assigned customer
