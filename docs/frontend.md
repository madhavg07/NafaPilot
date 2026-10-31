# Merchant dashboard (M9)

A Next.js client for the existing backend. **It calculates nothing authoritative.** Every profit figure, policy result, verdict and interval it shows comes from the API.

Data is synthetic, and execution is simulated: the app sends nothing to customers or to Paytm.

## Pages

| Route | Shows | Endpoints |
|---|---|---|
| `/` | Merchant list; backend and AI status | `GET /merchants`, `/health`, `/llm/status` |
| `/merchants/{id}` | Observed 28-day baseline and daily chart; coverage limits; detected opportunities with a labelled 4-week projection; policy; saved preferences; campaigns with predicted and measured results side by side; the recommendation form | `GET /merchants/{id}`, `/baseline`, `/preferences`, `GET /campaigns?merchant_id=`, `GET /campaigns/{id}`, `GET /experiments/{id}`, `POST /merchants/{id}/recommendations` |
| `/recommendations/{run_id}` | The rule's selection; each AI candidate with its prediction and policy snapshot; explanations with their provenance; "explain again" | `GET /recommendations/{id}`, `POST /recommendations/{id}/explanations` |
| `/campaigns/{id}` | Workflow rail; prediction; policy rules; inputs; edit as a new version; approve or reject (with an optional explicit preference); simulated execution; version history and audit trail | `GET /campaigns/{id}`, `/versions`, `/audit`, `POST /versions`, `POST /versions/{n}/decision`, `POST /versions/{n}/execute` |
| `/experiments/{id}` | Verdicts; prediction, observed outcomes, incremental effect and measured profit (all separate); caveats; assignment, exclusions and simulation settings; paginated customer outcomes | `GET /experiments/{id}`, `/assignments` |

## Visual design

The design is a quiet "ledger" layout, not a stack of cards:

- **Sections:** each one has its label in a left margin column and its content on the right, separated by thin rules. On phones they stack.
- **Typeface:** IBM Plex Sans, self-hosted through `@fontsource/ibm-plex-sans`, so builds and demos work offline. All figures use tabular numerals.
- **Colour:** the palette is near-monochrome ink on paper, with tokens in `app/globals.css` (light and dark). Colour is reserved for meaning:
  - green and red for gains and losses, and for verdicts;
  - amber for predictions;
  - violet for simulated results.
- **Buttons:** primary buttons are solid ink; the simulation button is violet.
- **Status:** shown as a small dot plus text, not a pill.

## Kinds of numbers

Each kind of number carries a consistent marker, so a reader can always tell where a figure came from:

- **Prediction:** dashed amber edge.
- **Simulated:** hatched violet edge.
- **Estimate with uncertainty:** solid slate edge.
- **Observed synthetic history:** solid grey edge.
- **Projection:** dotted edge.

Each block also has a text tag, so the meaning does not depend on colour.

## Simulated execution

- **Starting a run:** the button reads **Run simulation…**. It is enabled only when the backend reports the current version as approved, permitted and not yet executed. When it is disabled, the reason is shown beside it.
- **Confirmation:** before anything is sent, a confirmation panel shows:
  - the merchant;
  - the campaign and version;
  - the predicted profit and its range;
  - the policy holdout percentage;
  - a statement that this is a synthetic simulation only.
- **Idempotency key:**
  - A fresh `Idempotency-Key` is created when the user confirms a new run.
  - After a network error or a 5xx, **Retry the same request** re-sends the *same* key, so a run that did complete on the server is returned rather than duplicated.
  - A refusal (4xx), changed assumptions, or Cancel discards the key.
- **Duplicate clicks:** the button is disabled while a request is pending, and a ref guard blocks a second request before React re-renders.
- **Success:** shown only after the server returns the experiment.
- **Refusals:** each one (`not_approved`, `stale_version`, `policy_blocked`, `already_executed`, `idempotency_key_reused`, `insufficient_data`, …) gets a plain-language title and next step. The raw code, message, HTTP status and details stay available under *Technical details*.

## Integration notes

- **Run link:** there is no endpoint that lists recommendation runs. A campaign links back to its run through the `recommendation_run_id` in its `campaign_created` audit event.
- **Approval status in the campaign list:** `GET /campaigns` summaries carry no approval status, so the merchant page loads the details of the 20 most recently updated campaigns.
- **Campaign costs:** the AI does not supply them. The recommendation form starts empty and never fills in default costs.

## Tests

`npm test` runs Vitest with Testing Library in jsdom. Fetch is stubbed, and the fixtures in `tests/fixtures/` are real responses captured from the M8 backend. One test derives a "no interval" case from a real fixture by setting the interval fields to null.
