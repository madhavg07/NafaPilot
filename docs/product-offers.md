# Product-level offers

## Pipeline

AI proposes → code checks products → code evaluates whole baskets → policy → merchant approves → simulated holdout test → measured result

| Step | Who | What |
|---|---|---|
| Product selection | Merchant | Chooses which products may appear in offers (`merchant_product_selections`, append-only). Default: all. |
| Evidence | Code | Per-product 28-day units, margin, trend vs the previous 28 days; co-purchase pairs (support, confidence, lift, min 10 orders together); basket stats. |
| Proposal | Sarvam, then Gemini | `PromotionCandidateSet`: kind, SKUs, discount, optional cart minimum, optional hours / weekdays, extra-order guesses. Only selected SKUs are in the context. |
| Filtering | Code | Offers naming unselected products, kinds not requested, or discounts above the saved maximum are set aside with a reason. |
| Inputs | Code (`services/promotions/engine.py`) | Qualifying past orders Q (last 28 days, in the time slot, using the allocation rules). AOV/VC = averages of those whole baskets. Leakage S = Q × {1.0, 0.8, 0.6}. Extra orders I = AI guess, capped at 2Q + 10. |
| Profit | Code (unchanged M5 engine) | ΔP = I × (AOV − d − VC) − S × d − C per scenario. |
| Policy | Code | Discount cap vs the product/bundle price (cart: vs the minimum); margin on the discounted products (cart: on the average qualifying basket); loss limit on the policy's risk scenario; budget. |
| Selection | Code | Highest expected profit among permitted offers; ties → higher conservative profit → smaller discount; none positive → no offer. |

## Allocation rules (basket check and qualifying orders)

1. Product offers first, then bundles, then cart offers (fixed order).
2. A product or bundle offer applies once per order, to the first free units.
3. One unit never gets two discounts.
4. A cart offer counts only units that are unused and selected by the merchant; if that
   subtotal reaches the minimum the discount applies once (capped at the subtotal).
5. The basket's contribution before and after discounts is reported.

## Experiments

Each approved offer is tested on its own: identified existing customers are split 80/20
into treatment (this offer only) and holdout (no offer), so customers are never in two
offers' tests at once. Execution is refused if a product in the offer has since been
removed from the merchant's selection.

## Limits

- Extra orders are AI estimates (labelled "not verified"); leakage and basket values come
  from data.
- Incremental orders are valued as average qualifying baskets; upsell within an existing
  order is not modelled separately.
- Payment records alone (e.g. QR payments) usually lack item detail; product offers need
  item-level billing/POS data from an authorised source. Everything here is synthetic.
- No stock/inventory data is used.
