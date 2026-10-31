# Synthetic dataset

Everything in `data/synthetic/` is **invented**. The merchants, customers, orders and
campaigns are not real, and none of it is Paytm data. The data shows that the software
works; it is not evidence of how real merchants or customers behave.

Generate it (backend folder, venv active):

```powershell
python ..\scripts\generate_dataset.py            # seed 42
python ..\scripts\generate_dataset.py --seed 7   # a different but equally valid dataset
```

The same seed always produces byte-identical files on any OS. `manifest.json` records the
seed, row counts and a SHA-256 hash of every file. Code: `backend/app/synthetic/`.

## Time frame

| Period | Dates | Contents |
|---|---|---|
| Campaign history | from 2026-05-04 | Past campaigns, their holdout assignments and outcomes |
| Order window | 2026-08-03 (Mon) to 2026-10-25 (Sun), 12 weeks | `orders.csv` |

Campaigns finished before the order window starts, so `orders.csv` contains no campaign
orders (`discount_inr` is always 0). Campaign results live only in `assignments.csv` and
`outcomes.csv`.

## Tables

| File | One row per | Key columns |
|---|---|---|
| `merchants.csv` | merchant (20) | `merchant_id`, `average_order_value_inr`, `variable_cost_per_order_inr` (blank = unknown), `scenario_tag`, `history_start` |
| `merchant_policies.csv` | merchant policy version | `discount_cap_pct`, `min_margin_pct`, `max_loss_inr`, `budget_inr`, `holdout_pct`, `risk_scenario` |
| `customers.csv` | synthetic customer | `customer_id` (`M001-C0001`), `merchant_id`, `first_order_date`, `orders_in_window`, `segment` |
| `orders.csv` | order | `order_ts`, `order_date`, `weekday`, `hour`, `order_value_inr`, `variable_cost_inr` (blank = not recorded), `customer_id` (blank = walk-in), `is_repeat` (blank = unknown) |
| `campaigns.csv` | past campaign | `discount_inr`, `campaign_cost_inr`, `eligible_customers`, `holdout_pct`, the `true_*` ground-truth columns, `outcome_label` |
| `assignments.csv` | campaign × customer | `group` = `treatment` or `holdout` |
| `outcomes.csv` | campaign × customer | `orders`, `redemptions`, `gross_revenue_inr`, `discount_given_inr`, `is_simulated` |
| `feedback.csv` | merchant decision | `decision` (approved / rejected / edited), `reason`, `preference_key`, `preference_value` |
| `invalid_orders.csv` | deliberately broken order | `expected_issue`; for validation tests only, never loaded as real orders |

## Campaign simulation model (ground truth)

For each eligible customer, over the campaign window:

- **Base orders:** Poisson(`true_base_rate`). These would have happened without the offer.
- **Extra orders:** Poisson(`true_lift_per_customer`), treatment group only. These are caused by the offer.
- **Redemptions:** every extra order redeems the discount. Each treated base order redeems with probability `true_redeem_share_base`. Holdout customers never redeem.

`true_expected_incremental_profit_inr` uses the project's accounting convention, where the
discount is counted once, inside net revenue:

```
I  = treated customers × lift                       (incremental orders)
S  = treated customers × base_rate × redeem_share   (discounted orders that would have happened anyway)
ΔP = I × (AOV − discount − variable cost) − S × discount − campaign cost
```

`outcome_label` is checked in this order. It labels what the campaign truly was, and later
tests check that NafaPilot reaches the same verdict.

| Label | Meaning |
|---|---|
| `discount_cap_breach` | Discount above the merchant's cap |
| `null_effect` | True lift is zero; any measured uplift is noise |
| `loss_limit_violation` | Expected loss larger than `max_loss_inr` |
| `unprofitable` | Loses money, but within the loss limit |
| `profitable` | Positive expected incremental profit |

## Planted scenarios

| Merchant / record | Scenario | What it tests |
|---|---|---|
| M001 Anand Café | Weekday 14:00–17:00 orders/day: 38 for 4 weeks, then 38, 38, 37, 36, 34, 33, 31, 30 | Opportunity detection. Matches the deck's slide 2 exactly. AOV ≈ ₹200, cost ≈ ₹110, cap 15%, loss limit ₹1,000, holdout 20% |
| C001 (M001) | ₹20 targeted offer, 300 eligible, 240 treated / 60 held out, true lift 0.20, expected profit ₹1,360 | Experiment measurement. Analogue of the deck's slide 6 |
| C002 (M002) | 25% discount against a 15% cap | Policy breach |
| C003 (M003) | Expected −₹140 | Unprofitable but inside the loss limit |
| C004 (M004) | Expected −₹14,700 | Loss-limit block |
| C005 (M006) | True lift 0 | Inconclusive or null result |
| M007 | No variable cost at all | `insufficient_data` for profit |
| M011 | 30% of orders lack cost, 60% lack customer id | Unreliable data warnings |
| M015 | Only 3 weeks of history | Too little history to compare periods |
| M019 | 10 consecutive days with no records | Reporting a no-order period. The data has no operating-day calendar, so analytics excludes those days from rates and does not say whether they were closures, outages or zero sales |
| M003, M010 / M004, M017 | Growing / declining trends | Trend detection without assuming a cause |
| Feedback, M001 | Rejection: "don't discount existing customers" → preference `target=new_customers` | Preference learning |
| Feedback, M003 | Rejection with no reason | Record it, learn nothing about why |
| Feedback, M005 | Edit: discount lowered to ₹30 | Edits trigger a new version |
| Feedback, M009 | Strategic rejection (festival rush), no preference | A valid reason that should not become a constraint |
