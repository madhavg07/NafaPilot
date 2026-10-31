# Persistence (M6)

PostgreSQL via SQLAlchemy 2 and Alembic. Code: `backend/app/db/`,
`backend/app/services/campaign_store.py`, `backend/migrations/`.

```powershell
docker compose up -d                 # PostgreSQL 16 (project root)
cd backend
alembic upgrade head                 # create tables
alembic downgrade -1                 # undo (drops M6 tables and their rows)
```

Merchants, orders and policies still come from the synthetic dataset (CSV) through
`DataRepository`. The API checks `merchant_id` there, so it is an indexed column rather
than a foreign key.

## Tables (migration `0001_campaign_persistence`)

| Table | Mutability | Purpose and key constraints |
|---|---|---|
| `campaigns` | Mutable pointer | Identity, merchant, current version, `execution_status` (CHECK: `not_executed`, or `simulated` after an M8 simulated execution), `data_source`, `is_synthetic` |
| `campaign_versions` | Append-only | Immutable inputs (JSON, exact decimal strings) and `input_hash`. UNIQUE `(campaign_id, version)`; UNIQUE `(id, input_hash)`; CHECK on hash length and version ≥ 1 |
| `campaign_evaluations` | Append-only | M5 economics and policy decision per version. UNIQUE `(version, trigger)` with trigger `version_created` or `decision_recorded`; CHECK `result_kind = 'prediction'`; CHECK `NOT executable OR permitted` |
| `campaign_approvals` | Append-only | One decision per version (UNIQUE). **Composite FK `(campaign_version_id, input_hash)` → `campaign_versions(id, input_hash)`**, so an approval can only reference the exact hash of the version it names |
| `audit_events` | Append-only | `BIGSERIAL` id, actor, event type, merchant, campaign, version, JSON payload |

**How append-only is enforced:**
1. There are no update or delete code paths.
2. An ORM `before_flush` guard raises `ImmutableRecordError`.
3. Database triggers created by the migration reject `UPDATE` and `DELETE` (a PL/pgSQL function on PostgreSQL, `RAISE(ABORT)` triggers on SQLite).

`TRUNCATE` by a database owner is not blocked.

## Workflow rules

- **Create** stores version 1, evaluates it with the unchanged M5 engines and writes the audit events, all in one transaction.
- **Edit** is a full replacement of the inputs and creates version n+1. Identical inputs are refused (`no_change`).
  - The new version is pending; an approval of an earlier version never carries over.
  - If the previous version was approved, an `approval_superseded` event is recorded.
- **Decide** works only on the current version, with the `input_hash` the merchant reviewed. Each of these is refused (409) and audited as `decision_refused`:

  | Refusal | When |
  |---|---|
  | `stale_version` | The version is not the current one |
  | `input_hash_mismatch` | The submitted hash differs from the version's |
  | `already_decided` | The version already has a decision |
  | `policy_blocked` | Approving while any deterministic policy rule fails. Approval never overrides policy |

  A rejection is allowed even when policy blocks the campaign.
- **Approval is not execution.** `executable: true` means permitted and approved for these exact inputs; `execution_status` stays `not_executed`. Simulated execution (M8, `docs/experiments.md`) is a separate, explicit request.
- **Transactions:** every write runs in one transaction. A failure leaves no partial campaign, version, evaluation or audit row. A refused decision is audited in its own transaction after the decision transaction rolls back.
- **Audit payloads** are built from explicit fields only: versions, hashes, policy outcomes and the merchant's stated reason. They never include request bodies, headers or settings.

## API (`/api/v1/campaigns`)

| Method | Path | Result |
|---|---|---|
| POST | `/campaigns/evaluate` | Unchanged from M5: stateless, stores nothing |
| POST | `/campaigns` | Create (201), returns the campaign with version 1 evaluated |
| GET | `/campaigns?merchant_id=` | List |
| GET | `/campaigns/{id}` | Current version, latest evaluation, decision, `approval_status`, `executable` |
| GET | `/campaigns/{id}/versions` | All versions, oldest first |
| POST | `/campaigns/{id}/versions` | Edit: new version (201) |
| GET | `/campaigns/{id}/versions/{n}` | One version |
| POST | `/campaigns/{id}/versions/{n}/decision` | `{decision, input_hash, decided_by, reason}` |
| GET | `/campaigns/{id}/audit` | Append-only history |

Errors use `{error_code, message, details?}`: 404 for `campaign_not_found`, `version_not_found` and `merchant_not_found`; 409 for the refusals above; 422 for invalid request bodies and `policy_missing`.

## Tests

`backend/tests/db/` runs every test against a freshly migrated SQLite file. With
`TEST_DATABASE_URL` set to a **dedicated, disposable** PostgreSQL database (the fixture
downgrades it to base), the same tests also run on PostgreSQL:

```powershell
$env:TEST_DATABASE_URL="postgresql+psycopg://nafapilot:nafapilot@localhost:5432/nafapilot_test"
pytest tests/db
```
