# Decision engine: profit and policy

Code: `backend/app/services/decision/`. Both engines are deterministic, pure Python with
exact `Decimal` arithmetic. They do no I/O, call no LLM and do not touch the database.
Every result is a **prediction** from assumed order counts. It is not an observed or
forecast outcome.

## Profit engine

### Formula (the project's documented convention)

```
ΔR = I × (AOV − d) − S × d          incremental net revenue
ΔP = ΔR − I × VC − C                incremental contribution profit
I* = ceil((S × d + C) / (AOV − d − VC))   break-even incremental orders
```

| Symbol | Meaning |
|---|---|
| I | Incremental orders: orders that would not have happened without the offer |
| S | Existing orders that receive the discount: orders that would have happened anyway |
| d | Discount per redeemed order |
| AOV | Average order value (list price) |
| VC | Variable cost per order |
| C | Campaign costs other than the discount (messages, printing) |

### Accounting interpretation

- **Incremental orders** are booked at the discounted price: `I × (AOV − d)`.
- **Existing discounted orders** (S) would have been paid in full anyway, so their discount, `S × d`, is lost revenue. This is the only cannibalisation term.
- **Variable cost** is charged on incremental orders only (`I × VC`). Existing orders incur their cost with or without the campaign.
- **The discount is counted exactly once,** inside ΔR. `discount_cost_inr = (I + S) × d` is reported so the merchant can see the total discount handed out, but it is never subtracted again.
- **Example (Campaign A, conservative):** `30 × (200 − 20) − 50 × 20 = ₹4,400`; then `4,400 − 30 × 110 − 1,000 = +₹100`.

**What this formula does not model:**
- existing customers who redeem without changing behaviour beyond S;
- orders pulled forward from later weeks;
- effects on non-discounted orders;
- any change in order value caused by the offer.

These all live in the I and S assumptions. That's why the conservative and optimistic scenarios exist and why M8 measures outcomes against a holdout.

### Break-even

- **Definition:** the smallest whole I at which ΔP ≥ 0, holding S fixed. It is computed with exact fractions before the ceiling is taken, so an exact quotient (B: 6,000 / 60 = 100) is never rounded up to 101.
- **Not reachable:** if `AOV − d − VC ≤ 0`, each incremental order earns nothing or loses money, so no number of orders can recover the cost. Break-even is then `None` and `break_even_status = "not_reachable"`.
- **Campaign-level figure:** the campaign's break-even uses the expected scenario's S. Each scenario also reports its own.

`orders_to_exceed(target, …)` gives the exact I where ΔP equals a target. B beats A's expected ₹2,400 only when `I > (2,400 + 160 × 30 + 1,200) / 60 = 140`. At exactly 140 the two are equal.

### Inputs and rounding

- **Missing input:** a missing AOV, VC, d, C, I or S gives `status = "insufficient_data"` with one issue per field. No value is ever defaulted.
- **Invalid input:** gives `status = "invalid_input"`. Invalid means:
  - a negative value;
  - AOV = 0;
  - a discount at or above AOV;
  - conservative > expected > optimistic incremental orders (out of order).
- **Rounding:** money is rounded to paise (ROUND_HALF_UP) only on final values; intermediate values keep full precision. I and S may be fractional expectations.
- **Approval binding:** `input_hash` is a SHA-256 of the canonical inputs (₹200 and ₹200.00 hash the same). Approvals bind to it.

### Deck regression values (illustrative, synthetic)

| Campaign | d | C | (I, S) conservative / expected / optimistic | ΔP | Break-even |
|---|---|---|---|---|---|
| A targeted | ₹20 | ₹1,000 | (30, 50) / (60, 40) / (90, 30) | +₹100 / +₹2,400 / +₹4,700 | 26 |
| B off-peak | ₹30 | ₹1,200 | (60, 200) / (120, 160) / (180, 140) | −₹3,600 / +₹1,200 / +₹5,400 | 100 |

## Policy engine

It implements exactly the six rules in the project requirements, using the merchant's
policy record (`merchant_policies`). No other constraints are added.

| Rule | Blocks | Pass condition |
|---|---|---|
| `valid_inputs` | recommendation | The profit engine returned `ok` |
| `discount_cap` | recommendation | `d / AOV × 100 ≤ discount_cap_pct` |
| `min_post_discount_margin` | recommendation | `(AOV − d − VC) / (AOV − d) × 100 ≥ min_margin_pct` |
| `max_loss` | recommendation | ΔP in the policy's `risk_scenario` ≥ −`max_loss_inr` |
| `campaign_budget` | recommendation | `C ≤ budget_inr` |
| `merchant_approval` | execution | An approval exists for exactly this `input_hash` |

- **Permitted** = every recommendation rule passed. **Executable** = permitted and approved for these exact inputs.
- **Edits void approval:** any edit changes the hash, so an earlier approval no longer applies.
- **Approval can't override a violation:** an approval never makes a blocked campaign executable.
- **Limits are inclusive:** a value exactly at a cap, floor or limit passes.
- **Invalid inputs:** when inputs are incomplete or invalid, the other economic rules fail with "Not evaluated", never a guessed pass.
- **`holdout_pct`** is part of the policy record but is not a rule. The experiment engine (M8) uses it.

### Interpretations to review

The requirements name these two rules without defining them precisely:

1. **Post-discount margin.** It is taken as contribution margin on the price the customer actually pays, `(AOV − d − VC) / (AOV − d)`. With M001's 25% floor, A is 38.89% and B is 35.29%, so both pass under either plausible definition. The other definition, `(AOV − d − VC) / AOV`, gives 35% and 30%.
2. **Campaign budget.** It is applied to C, the cash spent besides the discount. The discount is forgone revenue already inside ΔR, and its downside is governed by `max_loss`. Counting B's discount outlay (up to ₹9,600 optimistic) against M001's ₹5,000 budget would add a second block that the deck doesn't show. The deck says B is blocked by the loss limit only.

### Independence from the LLM

- **Only numbers cross over.** `adapters.input_from_candidate` copies the campaign type, discount and I/S assumptions from a schema-validated LLM candidate. It drops the rationale and cited facts.
- **No invented cost.** The LLM schema has no campaign cost, and the adapter doesn't invent one; the caller must supply it.
- **Tests cover this.** They check that an LLM-suggested Campaign B, even with a rationale claiming it is "approved" and "safe", is still blocked by `max_loss`. They also check that the decision modules import no LLM or I/O code.

## API

`POST /api/v1/campaigns/evaluate` is stateless. It returns the economics and the policy decision for a complete `CampaignInput`, using the merchant's stored policy. Nothing is persisted, and the response is labelled `execution_mode: "not_executed"`. Versions, persistence and the approval workflow come in M6.
