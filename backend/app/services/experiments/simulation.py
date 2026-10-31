"""Deterministic outcome simulator. Same seed + parameters + assignment = same outcomes.

Model (the synthetic dataset's campaign convention, data/README.md), per assigned
customer over the campaign window:
- Holdout: orders ~ Poisson(base_rate); never redeems (never saw the offer).
- Treatment, lift >= 0: base ~ Poisson(base_rate), extra ~ Poisson(lift);
  orders = base + extra; every extra order redeems; each base order redeems with
  probability redeem_share.
- Treatment, lift < 0 (offer backfires): orders ~ Poisson(max(base_rate + lift, 0));
  each order redeems with probability redeem_share.
- Each outcome is independently unmeasured with probability missing_share (stored as
  NULL, never imputed).

These are SIMULATED outcomes from assumed parameters, not observations of real
customers. If the parameters come from the campaign's own prediction, a matching
result shows the measurement pipeline works, not that the prediction is right.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.experiments.assignment import AssignedCustomer


@dataclass(frozen=True)
class SimulationParams:
    base_rate: float
    lift: float
    redeem_share: float
    missing_share: float


@dataclass(frozen=True)
class SimulatedOutcome:
    customer_id: str
    group: str
    orders: int | None
    redemptions: int | None


def simulate(
    assigned: list[AssignedCustomer], params: SimulationParams, seed: str
) -> list[SimulatedOutcome]:
    rng = np.random.default_rng(int(seed[:16], 16))
    people = sorted(assigned, key=lambda a: a.customer_id)
    n = len(people)
    base = rng.poisson(params.base_rate, n)
    extra = rng.poisson(max(params.lift, 0.0), n)
    lowered = rng.poisson(max(params.base_rate + params.lift, 0.0), n)
    redeem_base = rng.binomial(base, params.redeem_share)
    redeem_lowered = rng.binomial(lowered, params.redeem_share)
    missing = rng.random(n) < params.missing_share

    out = []
    for i, a in enumerate(people):
        if missing[i]:
            out.append(SimulatedOutcome(a.customer_id, a.group, None, None))
        elif a.group == "holdout":
            out.append(SimulatedOutcome(a.customer_id, a.group, int(base[i]), 0))
        elif params.lift >= 0:
            out.append(
                SimulatedOutcome(
                    a.customer_id,
                    a.group,
                    int(base[i] + extra[i]),
                    int(extra[i] + redeem_base[i]),
                )
            )
        else:
            out.append(
                SimulatedOutcome(a.customer_id, a.group, int(lowered[i]), int(redeem_lowered[i]))
            )
    return out
