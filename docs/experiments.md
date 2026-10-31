# Simulated execution and measurement (M8)

> **Everything here is simulated.** No offer is sent, and Paytm and other real systems are never contacted.
> Outcomes are drawn from a model of the synthetic dataset. They are **not observations of real customers**, and a positive result does **not** show that a real campaign would increase sales.

## Flow

```
approved current version ──► POST /campaigns/{id}/versions/{n}/execute  (Idempotency-Key)
   │ eligibility checks (one locked transaction)
   ▼
eligible customers ─► hash-ordered 80/20 split ─► simulated outcomes ─► Welch interval ─► measured profit
   │                                                                                      │
   └─────────────── executions · assignments · outcomes · measurements (append-only) ◄────┘
```

## Eligibility to execute

Execution checks the following conditions in order. Each failure is refused with an `execution_refused` audit event, and nothing else is written.

| Check | Error |
|---|---|
| The campaign exists | 404 `campaign_not_found` |
| The version exists | 404 `version_not_found` |
| Key not used for another version | 409 `idempotency_key_reused` |
| Version not executed before | 409 `already_executed` (details carry the `experiment_id`) |
| The version is the current version | 409 `stale_version` |
| An **approved** decision is bound to this version's input hash | 409 `not_approved` |
| Every blocking policy rule passes when **re-checked against the merchant's current policy** | 409 `policy_blocked` (`blocked_by`) |
| A merchant policy exists | 422 `policy_missing` |
| Enough eligible customers: at least 30 per group | 422 `insufficient_data` (with counts and exclusions) |
| A base order rate can be calibrated | 422 `insufficient_data` |

Other rules:

- Approval alone never executes anything.
- An executed campaign cannot get new versions (`already_executed`). To try different inputs, start a new campaign.
- If a request fails part-way, everything is rolled back, an `execution_failed` audit event (error type only) is written, and the campaign stays `not_executed`. A failure is never reported as a success, and the same key can retry.

## Idempotency

| Level | Mechanism |
|---|---|
| Request | The `Idempotency-Key` header is required (8–128 characters). The same key on the same version returns the original experiment with HTTP 200 and `replayed: true`. Nothing is re-simulated, and an `execution_replayed` audit event is written. |
| Service | The campaign row is locked (`SELECT … FOR UPDATE` on PostgreSQL). Existing executions are looked up by key and by version before any write. |
| Database | `UNIQUE(executions.campaign_version_id)`, `UNIQUE(executions.idempotency_key)`, and primary key `(execution_id, customer_id)` on both assignments and outcomes. A racing request that hits one of these constraints is resolved exactly like a sequential retry. |

## Assignment

- **Seed:** `sha256("nafapilot-experiment:{campaign_version_id}:{input_hash}")`, stored on the execution.
- **Eligible customers**, from the synthetic customer list:
  - `targeted_offer` and `off_peak`: identified customers with at least one order in the window.
  - `new_customer`: listed customers with no order in the window.
- **Records that cannot be assigned** are counted in `assignment.excluded` and never given invented IDs:
  - customer records without an ID;
  - duplicate IDs, which are ambiguous, so every copy is excluded;
  - records without order history;
  - anonymous walk-in orders from the last 28 days.
- **Population cap (optional, `max_customers`):** the customers with the smallest `sha256("{seed}:population:{id}")`.
- **Split:** customers are ordered by `sha256("{seed}:split:{id}")`, and the first `round_half_up(n × holdout_pct / 100)` go to holdout.
  - `holdout_pct` comes from the merchant policy (20 in all synthetic policies).
  - The split is exact: 300 customers give 240/60, and M001's 1,109 give 887/222.
  - A customer's group depends only on the seed and their ID, so it does not change on retry.
- **Storage:** each assignment row stores the experiment ID, version ID, group and hash.
- **Holdout customers never receive treatment.** A DB CHECK enforces `holdout ⇒ redemptions = 0`, and a composite FK ties each outcome to its assignment's group.

## Outcome simulation

The simulation follows the dataset's campaign convention (`data/README.md`) and is deterministic per seed.

**Per customer, over 28 days:**

| Group | Orders | Redemptions |
|---|---|---|
| Holdout | `Poisson(base_rate)` | 0 |
| Treatment, lift ≥ 0 | `Poisson(base_rate) + Poisson(lift)` | Every extra order, plus each base order with probability `redeem_share` |
| Treatment, lift < 0 | `Poisson(max(base_rate + lift, 0))` | Each order with probability `redeem_share` |

- **Missing outcomes:** with probability `missing_outcome_share`, an outcome is stored as `NULL`. It is never imputed.

**Ground-truth parameters** are stored and labelled under `simulation` on the experiment:

| Parameter | Default | Override |
|---|---|---|
| `base_rate` | Mean orders per holdout customer in the merchant's past synthetic experiments. If there are none (M007, M015), the request is refused unless the parameter is supplied. | `simulation.base_rate_per_customer` |
| `lift` | The version's *expected* incremental orders ÷ treated customers | `simulation.true_lift_per_treated_customer` |
| `redeem_share` | Expected existing discounted orders ÷ treated base orders, capped to [0, 1] | `simulation.base_redeem_share` |

When the default lift is used, a caveat says that agreement with the prediction is **by construction**. That run tests the measurement pipeline, not the prediction.

## Estimator

- **Primary metric (pre-specified, single):** orders per assigned customer over the campaign window.
- **Effect:** mean(treatment) − mean(holdout). The 95% interval is **Welch's t interval** (unequal variances, `scipy.stats.ttest_ind(equal_var=False)`).
- **Incremental orders:** Î = effect × treated customers, with the interval scaled the same way.
- **Effect verdict:**
  - `positive` if the whole interval is above 0, and `negative` if it is below 0;
  - otherwise `inconclusive`, with reasons.
  - Reason codes: `interval_includes_zero`, `small_sample` (fewer than 30 measured customers in an arm; the interval is shown but no verdict is given), `no_variation`, and `insufficient_measured_outcomes` (fewer than 2 measured customers in an arm).
- **Reported alongside:** assigned, measured and missing counts per arm; the assignment proportion; SE, df and p-value; method; and confidence level.

## Measured profit

The formula uses measured quantities with the M5 accounting convention:

```
ΔP = Î × (AOV − VC) − R × d − C   ≡   Î × (AOV − d − VC) − (R − Î) × d − C
```

- **R** is the number of treatment redemptions. When outcomes are missing, R is scaled to all treated customers.
- **AOV and VC** are the version's merchant-entered settings; per-order values are not simulated.
- **Profit interval:** Î's interval mapped through the formula, with R treated as fixed.
- **Profit verdict:**
  - `profitable` or `loss` only when the whole interval is on one side of 0 and both arms have at least 30 measured customers;
  - otherwise `inconclusive`.
  - A `loss` can be decisive even when the order effect is not: for example, discounts paid on orders that would have happened anyway.

## The four kinds of numbers on an experiment

| Field | What it is | Source |
|---|---|---|
| `prediction` | M5 scenario forecast | Stored version evaluation, read-only and never modified |
| `observed` | Simulated orders and redemptions per group | `outcomes` rows (`is_simulated = true`) |
| `incremental` | Treatment-vs-holdout estimate with interval | `measurements.result` |
| `measured_profit` | Profit computed from the simulated observations | `measurements.result` |

Every response carries `is_simulated: true`, `mode: "simulated"`, `data_source`, `experiment_id`, the version and input hash, the method, `caveats`, and a notice.

## Assumptions and limits

- **Simulated data:** outcomes come from synthetic data with assumed parameters, so no real uplift is shown.
- **Welch approximation:** Welch's interval is approximate for skewed count data. The 30-per-arm floor keeps it reasonable, but not exact.
- **Missing outcomes:** assumed missing at random. If missingness depended on treatment or on behaviour, the estimate would be biased.
- **Multiple comparisons:**
  - one metric is tested once per experiment, so no correction is applied;
  - looking at many experiments or metrics and reporting only the best would need a multiple-comparison correction (e.g. Benjamini–Hochberg, as used for M4 alerts);
  - there is no sequential testing or early stopping.
- **No interference:** customers are assumed not to affect each other (SUTVA).
- **One execution per version:** reruns with a different seed are not offered.

## Persistence (migration `0003_simulated_execution`)

| Table | Key constraints |
|---|---|
| `executions` | UNIQUE version, UNIQUE idempotency key; composite FK `(version, input_hash)`; FK to the approval; CHECK `mode = 'simulated'`, `status = 'completed'`, counts consistent |
| `assignments` | PK `(execution_id, customer_id)`; FK `(execution_id, version)` → executions; CHECK on group |
| `outcomes` | PK `(execution_id, customer_id)`; FK `(execution_id, customer_id, group)` → assignments; CHECK `is_simulated`, orders and redemptions both set or both NULL, `0 ≤ redemptions ≤ orders`, holdout never redeems |
| `measurements` | PK/FK `execution_id`; CHECK on verdicts; `is_simulated` |

- **Append-only:** all four tables are append-only, enforced by the ORM guard and by database triggers.
- **Campaign status:** `campaigns.execution_status` CHECK now allows `not_executed` and `simulated`.
- **Downgrade:** the downgrade refuses to run while any campaign is `simulated`, so recorded executions are never silently lost.
