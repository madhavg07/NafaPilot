"""Simulated execution (M8): eligibility, refusals, idempotency, persistence, audit, rollback.

Runs on SQLite always and on PostgreSQL when TEST_DATABASE_URL is set. No Gemini calls.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.data import DataRepository
from app.db.models import (
    Assignment,
    Campaign,
    CampaignEvaluation,
    Execution,
    ImmutableRecordError,
    Measurement,
    Outcome,
)
from app.schemas.experiments import ExecuteRequest
from app.services import execution as svc
from tests.db.test_campaigns import URL, create, decide, edit, events
from tests.decision.fixtures import campaign, campaign_a

KEY = "key-00000001"


def execute(
    client: TestClient,
    c: dict[str, Any],
    key: str = KEY,
    version: int | None = None,
    **sim: Any,
) -> Any:
    v = version or c["current_version"]
    return client.post(
        f"{URL}/{c['campaign_id']}/versions/{v}/execute",
        headers={"Idempotency-Key": key},
        json={"requested_by": "merchant", "simulation": sim},
    )


def approved(client: TestClient, inp: Any = None, merchant: str = "M001") -> dict[str, Any]:
    c = create(client, inp or campaign_a(), merchant)
    assert decide(client, c).status_code == 200
    return c


def count(sessions: sessionmaker[Session], model: Any) -> int:
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


# ------------------------------------------------------------------- happy path


def test_execute_approved_version_and_keep_numbers_apart(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    c = approved(client)
    before = client.get(f"{URL}/{c['campaign_id']}/versions/1").json()
    r = execute(client, c)
    assert r.status_code == 201, r.text
    x = r.json()
    assert x["is_simulated"] is True and x["mode"] == "simulated" and x["status"] == "completed"
    assert x["data_source"] == "synthetic_in_memory" and "SIMULATED" in x["notice"]
    assert x["replayed"] is False and x["campaign_version"] == 1
    assert x["input_hash"] == c["current"]["input_hash"] == x["approval"]["input_hash"]

    # 80/20 by policy holdout_pct (1109 eligible → 887 / 222)
    a = x["assignment"]
    assert (a["eligible"], a["treatment"], a["holdout"], a["holdout_pct"]) == (1109, 887, 222, 20)
    assert x["incremental"]["assignment_proportion_treatment"] == round(887 / 1109, 6)

    # prediction, observation, incremental estimate and measured profit are separate
    assert x["prediction"]["result_kind"] == "prediction"
    assert [s["incremental_contribution_profit_inr"] for s in x["prediction"]["scenarios"]] == [
        100.0,
        2400.0,
        4700.0,
    ]
    assert x["observed"]["label"].startswith("simulated")
    assert x["observed"]["holdout"]["redemptions"] == 0
    assert x["incremental"]["method"] == "welch_difference_in_means"
    assert set(x["measured_profit"]) >= {"measured_incremental_profit_inr", "ci_low_inr", "formula"}
    assert x["effect_verdict"] in {"positive", "negative", "inconclusive"}
    assert any("by construction" in cv for cv in x["caveats"])
    assert any("not observations of real customers" in cv for cv in x["caveats"])

    # the prediction snapshot is untouched
    assert client.get(f"{URL}/{c['campaign_id']}/versions/1").json() == before
    camp = client.get(f"{URL}/{c['campaign_id']}").json()
    assert camp["execution_status"] == "simulated" and camp["experiment_id"] == x["experiment_id"]
    assert "SIMULATION" in camp["notice"]
    listed = client.get(URL).json()["campaigns"][0]
    assert listed["execution_status"] == "simulated"

    # persistence: one execution, one row per assigned customer, one measurement
    assert count(sessions, Execution) == 1 and count(sessions, Measurement) == 1
    assert count(sessions, Assignment) == count(sessions, Outcome) == 1109
    with sessions() as s:
        assert s.scalar(select(func.count()).where(Outcome.is_simulated.is_(False))) == 0
        bad = s.scalar(
            select(func.count())
            .select_from(Outcome)
            .where(Outcome.group == "holdout", Outcome.redemptions > 0)
        )
        assert bad == 0

    # reads
    exp_id = x["experiment_id"]
    got = client.get(f"/api/v1/experiments/{exp_id}").json()
    assert got == {**x, "replayed": False}
    assert client.get(f"{URL}/{c['campaign_id']}/experiments").json() == [got]
    page = client.get(f"/api/v1/experiments/{exp_id}/assignments?group=holdout&limit=5").json()
    assert page["total"] == 222 and page["is_simulated"] is True and len(page["items"]) == 5
    assert all(i["group"] == "holdout" and i["redemptions"] in (0, None) for i in page["items"])

    assert events(client, c)[-3:] == [
        "execution_requested",
        "execution_completed",
        "measurement_completed",
    ]


def test_assignments_and_outcomes_are_stable_on_replay(client: TestClient) -> None:
    c = approved(client)
    first = execute(client, c).json()
    page1 = client.get(f"/api/v1/experiments/{first['experiment_id']}/assignments?limit=1000")
    second = execute(client, c)
    assert second.status_code == 200
    again = second.json()
    assert again["replayed"] is True and again["experiment_id"] == first["experiment_id"]
    assert {**again, "replayed": False} == first
    page2 = client.get(f"/api/v1/experiments/{first['experiment_id']}/assignments?limit=1000")
    assert page1.json() == page2.json()
    assert events(client, c)[-2:] == ["execution_requested", "execution_replayed"]


# --------------------------------------------------------------------- refusals


def test_approval_is_required(client: TestClient, sessions: sessionmaker[Session]) -> None:
    c = create(client, campaign_a())
    r = execute(client, c)
    assert r.status_code == 409 and r.json()["error_code"] == "not_approved"
    rej = create(client, campaign_a())
    decide(client, rej, "rejected")
    r = execute(client, rej)
    assert r.status_code == 409 and r.json()["details"] == {"decision": "rejected"}
    assert count(sessions, Execution) == 0
    assert client.get(f"{URL}/{c['campaign_id']}").json()["execution_status"] == "not_executed"
    assert events(client, c)[-2:] == ["execution_requested", "execution_refused"]


def test_stale_version_refused(client: TestClient) -> None:
    c = approved(client)
    assert edit(client, c, campaign(discount=15)).status_code == 201
    r = execute(client, c, version=1)
    assert r.status_code == 409 and r.json()["error_code"] == "stale_version"
    r = execute(client, c, version=2)  # new version is not approved yet
    assert r.status_code == 409 and r.json()["error_code"] == "not_approved"


def test_policy_tightened_after_approval_blocks_execution(client: TestClient) -> None:
    c = approved(client)
    # The merchant lowers the campaign budget in their settings after approving.
    tightened = {
        "discount_cap_pct": "15",
        "min_margin_pct": "25",
        "max_loss_inr": "1000",
        "risk_scenario": "conservative",
        "budget_inr": "500",
        "holdout_pct": 20,
    }
    assert client.put("/api/v1/merchants/M001/settings", json=tightened).status_code == 200
    r = execute(client, c)
    assert r.status_code == 409 and r.json()["error_code"] == "policy_blocked"
    assert "campaign_budget" in r.json()["details"]["blocked_by"]
    payload = client.get(f"{URL}/{c['campaign_id']}/audit").json()[-1]["payload"]
    assert payload["error_code"] == "policy_blocked"


def test_already_executed_and_key_reuse(client: TestClient) -> None:
    c = approved(client)
    first = execute(client, c).json()
    r = execute(client, c, key="key-00000002")
    assert r.status_code == 409 and r.json()["error_code"] == "already_executed"
    assert r.json()["details"]["experiment_id"] == first["experiment_id"]
    other = approved(client)
    r = execute(client, other, key=KEY)
    assert r.status_code == 409 and r.json()["error_code"] == "idempotency_key_reused"
    # editing an executed campaign is refused too; the executed version stays current
    r = edit(client, c, campaign(discount=15))
    assert r.status_code == 409 and r.json()["error_code"] == "already_executed"


def test_missing_or_bad_idempotency_key(client: TestClient) -> None:
    c = approved(client)
    r = client.post(f"{URL}/{c['campaign_id']}/versions/1/execute", json={})
    assert r.status_code == 422
    assert execute(client, c, key="short").status_code == 422


def test_insufficient_customers_refused(client: TestClient) -> None:
    c = approved(client)
    r = execute(client, c, max_customers=100)  # 80 / 20 < 30 per arm
    assert r.status_code == 422 and r.json()["error_code"] == "insufficient_data"
    assert r.json()["details"]["holdout"] == 20
    ok = execute(client, c, key="key-00000003", max_customers=150)  # 120 / 30
    assert ok.status_code == 201 and ok.json()["assignment"]["holdout"] == 30


def test_no_base_rate_history_requires_explicit_assumption(client: TestClient) -> None:
    c = approved(client, merchant="M007")
    r = execute(client, c)
    assert r.status_code == 422 and r.json()["error_code"] == "insufficient_data"
    ok = execute(client, c, key="key-00000004", base_rate_per_customer="0.5")
    assert ok.status_code == 201
    assert ok.json()["simulation"]["base_rate_source"] == "request"


def test_unknown_campaign_and_experiment(client: TestClient) -> None:
    r = client.post(
        f"{URL}/{uuid.uuid4()}/versions/1/execute", headers={"Idempotency-Key": KEY}, json={}
    )
    assert r.status_code == 404
    assert client.get(f"/api/v1/experiments/{uuid.uuid4()}").status_code == 404


# -------------------------------------------------------------- effects via API


@pytest.mark.parametrize(
    ("lift", "effect"), [("0.6", "positive"), ("-0.25", "negative"), ("0", "inconclusive")]
)
def test_simulated_effect_direction(client: TestClient, lift: str, effect: str) -> None:
    c = approved(client)
    x = execute(client, c, true_lift_per_treated_customer=lift).json()
    assert x["effect_verdict"] == effect
    assert x["simulation"]["lift_source"] == "request"
    assert not any("by construction" in cv for cv in x["caveats"])


def test_missing_outcomes_are_stored_as_null(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    c = approved(client)
    x = execute(client, c, missing_outcome_share="0.3").json()
    with sessions() as s:
        nulls = s.scalar(select(func.count()).where(Outcome.orders.is_(None))) or 0
    obs = x["observed"]
    assert nulls == obs["treatment"]["missing_outcomes"] + obs["holdout"]["missing_outcomes"]
    assert 200 < nulls < 470
    assert any("missing at random" in cv for cv in x["caveats"])


# ------------------------------------------------- rollback, audit, constraints


def test_failure_rolls_back_everything_and_is_audited(
    sessions: sessionmaker[Session],
    repo: DataRepository,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = approved(client)

    def boom(**_: Any) -> Any:
        raise RuntimeError("measurement crashed")

    monkeypatch.setattr(svc, "measure", boom)
    with sessions() as s, pytest.raises(RuntimeError):
        svc.execute(s, repo, uuid.UUID(c["campaign_id"]), 1, KEY, ExecuteRequest())
    for model in (Execution, Assignment, Outcome, Measurement):
        assert count(sessions, model) == 0
    camp = client.get(f"{URL}/{c['campaign_id']}").json()
    assert camp["execution_status"] == "not_executed" and camp["experiment_id"] is None
    last = client.get(f"{URL}/{c['campaign_id']}/audit").json()[-1]
    assert last["event_type"] == "execution_failed"
    assert last["payload"]["error_type"] == "RuntimeError"
    monkeypatch.undo()
    assert execute(client, c).status_code == 201  # the same key can retry after a failure


def test_audit_payloads_hold_no_secrets(client: TestClient) -> None:
    c = approved(client)
    execute(client, c)
    execute(client, c, key="key-00000002")
    blob = json.dumps(client.get(f"{URL}/{c['campaign_id']}/audit").json()).lower()
    for word in ("api_key", "gemini", "password", "secret", "token", "authorization"):
        assert word not in blob


def test_evaluations_not_added_or_changed_by_execution(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    c = approved(client)
    with sessions() as s:
        before = [e.economics for e in s.scalars(select(CampaignEvaluation))]
    execute(client, c)
    with sessions() as s:
        assert [e.economics for e in s.scalars(select(CampaignEvaluation))] == before


def _executed(client: TestClient, sessions: sessionmaker[Session]) -> Execution:
    execute(client, approved(client))
    with sessions() as s:
        ex = s.scalars(select(Execution)).one()
        s.expunge(ex)
        return ex


def test_database_rejects_second_execution_of_a_version(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    ex = _executed(client, sessions)
    with sessions() as s, pytest.raises(IntegrityError), s.begin():
        s.add(
            Execution(
                id=uuid.uuid4(),
                campaign_id=ex.campaign_id,
                campaign_version_id=ex.campaign_version_id,
                input_hash=ex.input_hash,
                approval_id=ex.approval_id,
                idempotency_key="other-key-1",
                mode="simulated",
                status="completed",
                seed=ex.seed,
                holdout_pct=20,
                eligible_count=0,
                treatment_count=0,
                holdout_count=0,
                eligibility={},
                simulation={},
                requested_by="t",
                requested_at=ex.requested_at,
            )
        )


def test_database_outcome_constraints(client: TestClient, sessions: sessionmaker[Session]) -> None:
    ex = _executed(client, sessions)
    with sessions() as s:
        hold = s.scalars(select(Assignment).where(Assignment.group == "holdout")).first()
        assert hold is not None
        cid = hold.customer_id
    cases = [
        # duplicate outcome for an assigned customer
        Outcome(
            execution_id=ex.id,
            customer_id=cid,
            group="holdout",
            orders=1,
            redemptions=0,
            is_simulated=True,
        ),
        # outcome for a customer who was never assigned
        Outcome(
            execution_id=ex.id,
            customer_id="NOBODY",
            group="treatment",
            orders=1,
            redemptions=0,
            is_simulated=True,
        ),
    ]
    for row in cases:
        with sessions() as s, pytest.raises(IntegrityError), s.begin():
            s.add(row)


@pytest.mark.parametrize("table", ["executions", "assignments", "outcomes", "measurements"])
def test_experiment_tables_are_append_only(
    client: TestClient, sessions: sessionmaker[Session], table: str
) -> None:
    _executed(client, sessions)
    col = {
        "executions": "requested_by",
        "assignments": "assignment_hash",
        "outcomes": "orders",
        "measurements": "method",
    }[table]
    for sql in (f"UPDATE {table} SET {col} = {col}", f"DELETE FROM {table}"):
        with sessions() as s, pytest.raises(DBAPIError, match="append-only"), s.begin():
            s.execute(text(sql))
    with sessions() as s, pytest.raises(ImmutableRecordError), s.begin():
        m = s.scalars(select(Measurement)).one()
        m.effect_verdict = "positive" if m.effect_verdict != "positive" else "negative"
        s.flush()


def test_campaign_status_check_allows_only_known_values(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    c = create(client, campaign_a())
    with sessions() as s, pytest.raises(IntegrityError), s.begin():
        row = s.get(Campaign, uuid.UUID(c["campaign_id"]))
        assert row is not None
        row.execution_status = "executed"
        s.flush()


# ------------------------------------------------------------------ concurrency


@pytest.mark.parametrize("same_key", [True, False], ids=["same_key", "different_keys"])
def test_concurrent_requests_create_one_execution(
    db_url: str,
    client: TestClient,
    sessions: sessionmaker[Session],
    repo: DataRepository,
    same_key: bool,
) -> None:
    if not db_url.startswith("postgresql"):
        pytest.skip("real concurrency needs PostgreSQL")
    c = approved(client)
    cid = uuid.UUID(c["campaign_id"])
    results: list[Any] = []
    barrier = threading.Barrier(4)

    def worker(i: int) -> None:
        key = KEY if same_key else f"key-concurrent-{i}"
        with sessions() as s:
            barrier.wait()
            try:
                results.append(svc.execute(s, repo, cid, 1, key, ExecuteRequest()))
            except svc.CampaignError as err:
                results.append(err.error_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert count(sessions, Execution) == 1 and count(sessions, Measurement) == 1
    assert count(sessions, Outcome) == 1109
    wins = [r for r in results if isinstance(r, tuple)]
    if same_key:
        assert len(wins) == 4 and len({w[0] for w in wins}) == 1
        assert sum(not w[1] for w in wins) == 1  # exactly one non-replay
    else:
        assert len(wins) == 1 and results.count("already_executed") == 3
