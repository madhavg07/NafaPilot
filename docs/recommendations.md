# Recommendations, feedback and explanations (M7)

Code: `backend/app/services/recommendations.py`, `backend/app/services/llm/{grounding,templates}.py`,
migration `0002_recommendations_preferences`.

## Who decides what

| Step | Done by |
|---|---|
| Observed facts (e.g. the −21% afternoon decline, cause unknown) | M4 analytics |
| Candidate campaigns (type, discount, target, assumed I/S, rationale) | LLM (Gemini or mock) |
| Merchant preferences applied as filters | Deterministic code |
| Profit scenarios and break-even | M5 profit engine |
| Policy pass/fail | M5 policy engine |
| Which option is recommended | Fixed rule in code (below) |
| Wording of the explanation | LLM, only if grounded; otherwise a deterministic template |
| Approve / reject | Merchant (M6 decision endpoint) |

**Selection rule:** among candidates that pass every policy rule, pick the highest expected incremental contribution profit. Ties go to the higher conservative profit, then the smaller discount. If none has a positive expected profit, the recommendation is to do nothing.

## Run flow (`POST /api/v1/merchants/{id}/recommendations`)

1. **Read** stored preferences and the last 20 decisions, in a short read transaction.
2. **Build the LLM context** from:
   - M4 observed facts;
   - merchant-entered order value and variable cost (labelled `merchant_entered_settings`);
   - policy limits;
   - allowed campaign types (minus any avoided type);
   - preferences;
   - decision history.
3. **Generate candidates.** No transaction is held during the call. If it fails (quota, timeout, invalid output, not configured), the structured LLM error is returned and nothing is written.
4. **Process candidates in code:**
   - drop candidates that break an explicit preference (`filtered_by_preference`, with reasons);
   - evaluate the rest with M5. Campaign cost C comes only from `campaign_costs_inr` in the request. A type without a cost is `insufficient_data` and can't be recommended; nothing is invented.
5. **Select** by the rule above.
6. **Explain.** The explanation is generated from the evaluated records and the observed facts only, then the grounding check runs.
7. **Write** in one transaction:
   - the run (context, candidate snapshots, selection, learning labels);
   - one campaign per evaluated candidate (M6 version 1, `assumption_source = llm_suggestion`, linked by `recommendation_run_id`, pending approval);
   - the explanation;
   - the audit events.

## Grounded explanations

An LLM explanation is kept only if every number in its text appears in what it was given (records and observed facts). Numbers are compared by value, so "2,400", "2400" and "2400.00" match, and signs are ignored. Record ids are letters (A, B, …) so they add no digits.

Otherwise the deterministic template is stored, with `fallback_reason`:
- `ungrounded_numbers: …` when the text has numbers that aren't in the records;
- the LLM error code when the call failed;
- `no_evaluable_candidates` when nothing could be evaluated.

The database enforces `source = 'template' OR grounded`.

`POST /api/v1/recommendations/{run_id}/explanations` with `{lang: "en" | "hinglish", question}` adds another explanation of the same stored run (for example "Off-peak kyun nahi?"). It explains the run as decided at generation time.

## Feedback and preferences

- **Rejection reasons** are stored on the M6 decision and passed to the next run as decision history.
- **Preferences change only when the merchant sets one explicitly while rejecting** (`preference` on the decision body). The allowed choices:
  - `target`: `new_customers`, `existing_customers` or `all`;
  - `max_discount_inr`: a whole number of rupees;
  - `avoid_campaign_type`: any campaign type except `no_action`.
- **No inference:** free-text reasons are never turned into preferences by code or by the LLM.
- **Storage:** preferences are append-only rows; the latest row per key is current. `GET /api/v1/merchants/{id}/preferences` lists them.
- **Enforcement:** preferences go to the LLM as context and are also enforced in code, so an LLM that ignores them still can't get a filtered candidate stored or recommended.

## Learning labels (in every run)

- `model_retrained: false`
- **Preference learning:** stored preferences and past decisions were used as context and filters.
- **Performance learning:** not available until campaign outcomes are measured after simulated execution (M8).

## Storage (migration `0002`)

| Table | Mutability | Purpose |
|---|---|---|
| `recommendation_runs` | Append-only | Provider, model, prompt version, context hash, context and result snapshots |
| `explanations` | Append-only | Text, language, question, source, grounded flag, fallback reason |
| `merchant_preferences` | Append-only | Explicit preferences, linked to the rejected version |
| `campaigns.recommendation_run_id` | New nullable FK | Links generated campaigns to their run |

- **Upgrade:** existing campaigns are kept, with `recommendation_run_id` set to NULL.
- **Downgrade:** the new tables and the column are removed; campaign rows are kept.
- **SQLite:** the batch rebuild of `campaigns` runs with foreign-key enforcement switched off (Alembic's documented approach). PostgreSQL alters the table in place.
