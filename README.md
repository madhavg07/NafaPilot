# NafaPilot — Merchant Growth Decision Engine

Profit-aware campaign decisions for small merchants: detect an opportunity, compare
campaign options on **incremental contribution profit**, enforce the merchant's limits in
code, get approval, run a simulated holdout experiment and measure the result.

Team PayMatrix · Paytm Build for India AI Hackathon 2026 · Track 1: Merchant Growth AI.

> **Prototype.** All data is synthetic and campaign execution is simulated.
> This is an independent submission, not an official Paytm product, and it has no access
> to Paytm systems.

## Core principle

The LLM proposes; deterministic code decides. The LLM suggests campaign candidates and
writes explanations (English and Hinglish). Python calculates every profit figure, and
the policy engine can block any campaign. The LLM cannot override either.

## Repository layout

```
PAYMATRIX/
├── backend/            FastAPI app (Python)
│   ├── app/
│   │   ├── main.py     app entry, /health
│   │   ├── config.py   settings from environment variables
│   │   ├── api/v1/     versioned API routes
│   │   ├── schemas/    Pydantic request/response models
│   │   ├── services/   analytics, profit, policy, LLM, experiment engines
│   │   └── db/         SQLAlchemy models and session
│   └── tests/          pytest
├── frontend/           Next.js + TypeScript + Tailwind dashboard
├── scripts/            synthetic dataset generator
├── data/synthetic/     generated CSVs (not committed)
├── docker-compose.yml  local PostgreSQL 16
└── .env.example        copy to .env
```

## Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer
- PostgreSQL 16, through Docker Desktop (easiest) or a local install

## Setup (Windows PowerShell)

Run these from the project folder (`PAYMATRIX`).

**1. Environment file**

```powershell
Copy-Item .env.example .env
```

**2. Database**

```powershell
docker compose up -d
```

Without Docker, create a user `nafapilot` (password `nafapilot`) and a database
`nafapilot` in your local PostgreSQL, or change `DATABASE_URL` in `.env`.

**3. Backend**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python ..\scripts\generate_dataset.py      # synthetic data -> data\synthetic\ (see data/README.md)
alembic upgrade head                        # create campaign tables (see docs/persistence.md)
uvicorn app.main:app --reload --port 8000
```

Check it: open http://localhost:8000/health and the API docs at http://localhost:8000/docs.

If PowerShell blocks `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**4. Frontend** (in a second terminal)

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000. The home page lists the merchants and should say **Connected**.

The dashboard walks through the workflow (see `docs/frontend.md`):
merchant overview → **Get recommendations** (enter a campaign cost per type) → review a
campaign → **Approve** → **Run simulation…** (confirm) → experiment results.

On macOS or Linux, use `cp` instead of `Copy-Item` and `source .venv/bin/activate`.

## Checks

```powershell
# backend (inside backend/, venv active)
pytest
ruff check .
mypy app

# frontend (inside frontend/)
npm run lint
npm run typecheck
npm test          # Vitest + Testing Library; no backend or Gemini needed
npm run build
```

## API so far

Interactive docs: http://localhost:8000/docs

| Method | Path | What it returns |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/api/v1/meta` | Data source and execution mode labels |
| GET | `/api/v1/llm/status` | Configured LLM provider (never the key) |
| POST | `/api/v1/llm/ping` | Live LLM round-trip (disabled in production) |
| GET | `/api/v1/merchants` | Merchant list |
| GET | `/api/v1/merchants/{id}` | Profile and policy |
| GET | `/api/v1/merchants/{id}/baseline` | Baseline, weekly trends, data quality, opportunity alerts |
| POST | `/api/v1/data/validate` | Validation report for order rows (nothing stored) |
| POST | `/api/v1/campaigns/evaluate` | Predicted scenario profits and policy decision (stateless; see `docs/decision-engine.md`) |
| POST/GET | `/api/v1/campaigns`, `/{id}`, `/{id}/versions`, `/{id}/versions/{n}/decision`, `/{id}/audit` | Persisted campaigns, immutable versions, approvals bound to a version's input hash, audit trail (see `docs/persistence.md`) |
| POST | `/api/v1/merchants/{id}/recommendations` | LLM candidates → code-checked, evaluated, selected by rule, explained (grounded), stored (see `docs/recommendations.md`) |
| GET | `/api/v1/recommendations/{run_id}` | A stored recommendation run |
| POST | `/api/v1/recommendations/{run_id}/explanations` | Another explanation (English/Hinglish, optional question) |
| POST | `/api/v1/campaigns/{id}/versions/{n}/execute` | **Simulated** execution of the approved current version (`Idempotency-Key` header required): 80/20 holdout, simulated outcomes, Welch uplift interval, measured profit (see `docs/experiments.md`) |
| GET | `/api/v1/campaigns/{id}/experiments`, `/api/v1/experiments/{id}`, `/api/v1/experiments/{id}/assignments` | Stored simulated experiments and per-customer assignments/outcomes |
| GET | `/api/v1/merchants/{id}/preferences` | Explicit merchant preferences |
| GET | `/api/v1/merchants/{id}/products` | Product catalogue with 28-day sales, margins, trend, products bought together, and the merchant's product selection |
| PUT | `/api/v1/merchants/{id}/products/selection` | Save which products may appear in offers (new append-only version if it changed) |
| POST | `/api/v1/merchants/{id}/offers/recommendations` | Sarvam/Gemini propose product, bundle and cart offers on the selected products; code evaluates whole baskets, applies policy, picks one by rule (see `docs/product-offers.md`) |
| POST | `/api/v1/merchants/{id}/baskets/check` | Which discount each item in one basket gets from the given offers (exclusion rule) |
| POST | `/api/v1/campaigns/{id}/offer-versions` | Change an offer's discount (or cart minimum); code rebuilds the inputs; needs a new approval |
| GET/PUT | `/api/v1/merchants/{id}/settings` | The merchant's own limits, usual campaign costs and preferences, with the defaults. Each save that changes a limit or cost appends a new version (policy version + 1); earlier evaluations keep their version |

Opportunity alerts are a **screening heuristic**, not a calibrated model. They compare
the latest week and last two weeks with a reference period, using an exact binomial test
(which assumes Poisson-like order counts, so p-values are nominal) with
Benjamini-Hochberg control across time segments. Confidence labels are rules, not
probabilities, and the cause is always reported as unknown. The data has no record of
operating days, so rates are per day *with recorded orders*; days without orders are
reported, not assumed to be closures, outages or zero sales. The 4-week segment baseline
is a projection at the latest observed rate, not an observed result or a forecast.

## LLM provider

`LLM_PROVIDER` takes one provider or a comma-separated priority list. With
`LLM_PROVIDER=sarvam,gemini` the backend asks **Sarvam AI first** and, if Sarvam fails for
any reason (no key, bad key, out of credit, rate limit, timeout, server error, unusable
answer), **automatically retries the same request with Gemini**. The screen shows which
provider actually answered. If every provider fails, the API returns a clear error; it
never invents an answer. `mock` is only for automated tests and fully offline demos.

Edit the `.env` file in the project root (`PAYMATRIX\.env`, never the example file):

```
LLM_PROVIDER=sarvam,gemini
SARVAM_API_KEY=<your key from the Sarvam dashboard>
SARVAM_MODEL=sarvam-105b
SARVAM_REASONING_EFFORT=low
GEMINI_API_KEY=<your key from Google AI Studio>
GEMINI_MODEL=gemini-3.5-flash
LLM_TIMEOUT_SECONDS=60
LLM_MAX_OUTPUT_TOKENS=8192
```

If Sarvam rejects `reasoning_effort` for your model, leave `SARVAM_REASONING_EFFORT=`
empty. Then check the connection (backend folder, venv active):

```powershell
python ..\scripts\check_llm.py               # status of each provider, then which one will be used
python ..\scripts\check_llm.py --quick       # ping only (uses very few tokens)
python ..\scripts\check_llm.py --provider sarvam
```

Keys live only in the backend `.env` (git-ignored). They are never sent to the frontend,
returned by the API, or written to logs. `GET /api/v1/llm/status` reports each provider in
the chain and whether its key is present, without revealing it. On Render, set the same
variables under the service's **Environment** tab.

The LLM only proposes candidates and writes explanations. Its output is schema-validated,
and profit and policy decisions are made by deterministic Python that the LLM cannot
override.

## Merchant settings

On the shop page, **Limits and preferences → Edit settings** lets the merchant change the
biggest discount, minimum margin, largest acceptable loss, budget per offer, holdout share,
how careful the loss check is, the usual cost to run each kind of offer, and optional
preferences. Every field starts from a default (the dataset's policy; offer costs ₹1,000
targeted, ₹1,200 off-peak, ₹800 new-customer) and **Reset to defaults** restores them.
"Plan a campaign" is pre-filled with the saved offer costs. Settings are stored
append-only in `merchant_settings` (migration `0004_merchant_settings`; run
`python -m alembic upgrade head`).

## Product offers (product, bundle, cart)

The shipped synthetic dataset has **four shops** (Anand Café, Sharma General Store, Gupta
Sweets, Spice Route Kitchen), each with **15-18 products** in `products.csv` and every order
broken into lines in `order_items.csv` (order totals in `orders.csv` always equal the sum of
their lines). Regenerate it with `python ..\scripts\generate_dataset.py`. Four more
edge-case merchants exist only inside the automated tests.

1. On the shop page, **Your products**: the merchant ticks which products may be put on
   offer. The AI only sees those, and code rejects any offer naming another product.
2. **Plan a campaign → Get offer ideas**: Sarvam (Gemini as automatic backup) proposes
   product, bundle and cart offers using real "bought together" pairs and slots where
   orders fell.
3. Code finds the past orders (last 28 days, in the offer's time slot) each offer would
   have applied to, and uses those **whole baskets** for profit: customers who would have
   bought anyway get the discount too (leakage), and only the extra orders are new profit.
4. Policy checks the discount against the product/bundle price (or the cart minimum),
   the margin left, the loss limit on the bad case, and the budget.
5. **Try a basket** shows the exclusion rule: a unit never gets two discounts, and items
   already discounted by a product or bundle offer don't count towards a cart offer.
6. Approve one offer, then run a simulated experiment (80/20 holdout) as before.

The shop-wide (merchant-level) campaign flow is still available under "Shop-wide campaign
instead", and is used automatically if a shop has no product data. New migration
`0005_product_promotions` (run `python -m alembic upgrade head`).

## Deployment (planned)

- Frontend: Vercel
- Backend and PostgreSQL: Render

## Milestones

1. Project setup ✅
2. LLM provider foundation (Gemini + mock) ✅
3. Synthetic dataset generator ✅
4. Analytics and baseline engine ✅
5. Profit and policy engines ✅
6. Database and API ✅
7. Feedback loop and explanations ✅
8. Simulated execution and measurement ✅
9. Merchant dashboard ✅
10. End-to-end tests, deployment and offline fallback
