"""Persisted campaigns: round trips, versions, approval binding, refusals, audit, rollback."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.data import DataRepository
from app.db.models import (
    AuditEvent,
    Campaign,
    CampaignApproval,
    CampaignEvaluation,
    CampaignVersion,
)
from app.schemas.campaigns import CampaignCreate
from app.services import campaign_store as store
from app.services.decision import input_hash
from tests.decision.fixtures import campaign, campaign_a, campaign_b

URL = "/api/v1/campaigns"


def create(client: TestClient, inp: Any, merchant: str = "M001") -> dict[str, Any]:
    resp = client.post(URL, json={"merchant_id": merchant, "campaign": inp.model_dump(mode="json")})
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def decide(
    client: TestClient,
    c: dict[str, Any],
    decision: str = "approved",
    *,
    version: int | None = None,
    input_hash: str | None = None,
) -> Any:
    v = version or c["current_version"]
    h = input_hash or c["current"]["input_hash"]
    return client.post(
        f"{URL}/{c['campaign_id']}/versions/{v}/decision",
        json={"decision": decision, "input_hash": h, "reason": "ok"},
    )


def edit(client: TestClient, c: dict[str, Any], inp: Any) -> Any:
    return client.post(
        f"{URL}/{c['campaign_id']}/versions",
        json={"campaign": inp.model_dump(mode="json"), "change_note": "edit"},
    )


def events(client: TestClient, c: dict[str, Any]) -> list[str]:
    return [e["event_type"] for e in client.get(f"{URL}/{c['campaign_id']}/audit").json()]


# ------------------------------------------------------------------- round trip


def test_create_round_trip_preserves_inputs_results_and_labels(client: TestClient) -> None:
    c = create(client, campaign_a())
    assert c["current_version"] == 1 and c["approval_status"] == "pending"
    assert c["permitted"] is True and c["executable"] is False
    assert c["execution_status"] == "not_executed" and c["result_kind"] == "prediction"
    assert c["is_synthetic"] is True and c["data_source"] == "synthetic_in_memory"
    assert "nothing has been executed" in c["notice"]

    fetched = client.get(f"{URL}/{c['campaign_id']}").json()
    assert fetched == c
    cur = fetched["current"]
    assert cur["inputs"] == campaign_a().model_dump(mode="json")
    assert cur["input_hash"] == input_hash(campaign_a())
    econ = cur["evaluation"]["economics"]
    assert [s["incremental_contribution_profit_inr"] for s in econ["scenarios"]] == [
        100.0,
        2400.0,
        4700.0,
    ]
    assert econ["break_even_incremental_orders"] == 26
    assert econ["result_kind"] == "prediction"
    assert cur["evaluation"]["policy"]["blocked_by"] == ["merchant_approval"]
    assert events(client, c) == ["campaign_created", "version_created", "evaluation_recorded"]


def test_policy_blocked_campaign_is_stored_with_reasons(client: TestClient) -> None:
    c = create(client, campaign_b())
    assert c["permitted"] is False
    assert c["current"]["evaluation"]["policy"]["blocked_by"] == ["max_loss"]


def test_incomplete_inputs_are_stored_not_filled_in(client: TestClient) -> None:
    c = create(client, campaign(vc=None))
    econ = c["current"]["evaluation"]["economics"]
    assert econ["status"] == "insufficient_data" and econ["scenarios"] == []
    assert c["current"]["inputs"]["variable_cost_per_order_inr"] is None


def test_list_and_filter(client: TestClient) -> None:
    create(client, campaign_a())
    create(client, campaign_b(), merchant="M002")
    assert len(client.get(URL).json()["campaigns"]) == 2
    only = client.get(URL, params={"merchant_id": "M002"}).json()["campaigns"]
    assert [x["merchant_id"] for x in only] == ["M002"]


# ---------------------------------------------------------------- approvals


def test_approval_binds_to_version_and_does_not_execute(client: TestClient) -> None:
    c = create(client, campaign_a())
    resp = decide(client, c)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approval_status"] == "approved" and body["executable"] is True
    assert body["execution_status"] == "not_executed"  # approval is not execution
    assert body["current"]["decision"]["input_hash"] == c["current"]["input_hash"]
    assert body["current"]["evaluation"]["trigger"] == "decision_recorded"
    assert events(client, c)[-2:] == ["approval_recorded", "evaluation_recorded"]


def test_approval_cannot_override_policy(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    c = create(client, campaign_b())
    resp = decide(client, c)
    assert resp.status_code == 409
    err = resp.json()
    assert err["error_code"] == "policy_blocked"
    assert err["details"]["blocked_by"] == ["max_loss"]
    assert "−₹3,600" in err["details"]["reasons"]["max_loss"]
    after = client.get(f"{URL}/{c['campaign_id']}").json()
    assert after["approval_status"] == "pending" and after["executable"] is False
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(CampaignApproval)) == 0
    refused = client.get(f"{URL}/{c['campaign_id']}/audit").json()[-1]
    assert refused["event_type"] == "decision_refused"
    assert refused["payload"]["error_code"] == "policy_blocked"


def test_incomplete_campaign_cannot_be_approved(client: TestClient) -> None:
    c = create(client, campaign(vc=None))
    resp = decide(client, c)
    assert resp.status_code == 409 and "valid_inputs" in resp.json()["details"]["blocked_by"]


def test_wrong_hash_refused_and_audited(client: TestClient) -> None:
    c = create(client, campaign_a())
    resp = decide(client, c, input_hash="0" * 64)
    assert resp.status_code == 409 and resp.json()["error_code"] == "input_hash_mismatch"
    assert events(client, c)[-1] == "decision_refused"
    assert client.get(f"{URL}/{c['campaign_id']}").json()["approval_status"] == "pending"


def test_rejection_then_approval_is_already_decided(client: TestClient) -> None:
    c = create(client, campaign_b())  # rejecting a blocked campaign is allowed
    assert decide(client, c, "rejected").json()["approval_status"] == "rejected"
    again = decide(client, c, "approved")
    assert again.status_code == 409 and again.json()["error_code"] == "already_decided"


# ----------------------------------------------------------------- versions


def test_edit_creates_new_version_and_requires_reapproval(client: TestClient) -> None:
    c = create(client, campaign_a())
    v1_hash = c["current"]["input_hash"]
    decide(client, c)
    resp = edit(client, c, campaign(discount=25))
    assert resp.status_code == 201, resp.text
    c2 = resp.json()
    assert c2["current_version"] == 2
    assert c2["approval_status"] == "pending" and c2["executable"] is False
    assert c2["earlier_version_was_approved"] is True
    assert c2["current"]["input_hash"] != v1_hash

    v1 = client.get(f"{URL}/{c['campaign_id']}/versions/1").json()
    assert v1["is_current"] is False and v1["input_hash"] == v1_hash
    assert v1["decision"]["decision"] == "approved"  # history kept, but it is not v2's
    assert v1["inputs"]["discount_inr"] == "20"  # unchanged

    stale = decide(client, c2, version=1, input_hash=v1_hash)
    assert stale.status_code == 409 and stale.json()["error_code"] == "stale_version"
    reapproved = decide(client, c2)
    assert reapproved.json()["executable"] is True
    assert events(client, c)[5:8] == [
        "approval_superseded",
        "version_created",
        "evaluation_recorded",
    ]


def test_edit_can_turn_a_permitted_campaign_into_a_blocked_one(client: TestClient) -> None:
    c = create(client, campaign_a())
    c2 = edit(client, c, campaign_b()).json()
    assert c2["permitted"] is False and c2["campaign_type"] == "off_peak"
    assert decide(client, c2).status_code == 409


def test_identical_edit_is_rejected(client: TestClient) -> None:
    c = create(client, campaign_a())
    same = campaign(aov="200.00")  # same canonical inputs, different spelling
    resp = edit(client, c, same)
    assert resp.status_code == 409 and resp.json()["error_code"] == "no_change"


def test_versions_listed_in_order(client: TestClient) -> None:
    c = create(client, campaign_a())
    edit(client, c, campaign(discount=22))
    edit(client, c, campaign(discount=24))
    versions = client.get(f"{URL}/{c['campaign_id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert [v["is_current"] for v in versions] == [False, False, True]


# ------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("get", "/00000000-0000-0000-0000-000000000000", 404, "campaign_not_found"),
        ("get", "/00000000-0000-0000-0000-000000000000/audit", 404, "campaign_not_found"),
        ("get", "/{id}/versions/9", 404, "version_not_found"),
    ],
)
def test_not_found_errors(
    client: TestClient, method: str, path: str, status: int, code: str
) -> None:
    c = create(client, campaign_a())
    resp = getattr(client, method)(URL + path.format(id=c["campaign_id"]))
    assert resp.status_code == status and resp.json()["error_code"] == code


def test_unknown_merchant_and_bad_hash_format(client: TestClient) -> None:
    resp = client.post(
        URL, json={"merchant_id": "M999", "campaign": campaign_a().model_dump(mode="json")}
    )
    assert resp.status_code == 404 and resp.json()["error_code"] == "merchant_not_found"
    c = create(client, campaign_a())
    bad = client.post(
        f"{URL}/{c['campaign_id']}/versions/1/decision",
        json={"decision": "approved", "input_hash": "not-a-hash"},
    )
    assert bad.status_code == 422


def test_stateless_evaluate_still_stores_nothing(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    resp = client.post(
        f"{URL}/evaluate",
        json={"merchant_id": "M001", "campaign": campaign_a().model_dump(mode="json")},
    )
    assert resp.status_code == 200 and resp.json()["execution_mode"] == "not_executed"
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Campaign)) == 0
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == 0


# ------------------------------------------------------------- transactions


def test_failed_write_leaves_nothing_behind(
    monkeypatch: pytest.MonkeyPatch, repo: DataRepository, sessions: sessionmaker[Session]
) -> None:
    real = store._audit

    def failing(session: Session, event_type: str, **kw: Any) -> None:
        if event_type == "evaluation_recorded":
            raise RuntimeError("simulated failure mid-transaction")
        real(session, event_type, **kw)

    monkeypatch.setattr(store, "_audit", failing)
    body = CampaignCreate(merchant_id="M001", campaign=campaign_a())
    with sessions() as s, pytest.raises(RuntimeError):
        store.create_campaign(s, repo, body)
    with sessions() as s:
        for model in (Campaign, CampaignVersion, CampaignEvaluation, AuditEvent):
            assert s.scalar(select(func.count()).select_from(model)) == 0, model


def test_failed_edit_keeps_previous_version_current(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, sessions: sessionmaker[Session]
) -> None:
    c = create(client, campaign_a())
    real = store._audit

    def failing(session: Session, event_type: str, **kw: Any) -> None:
        if event_type == "version_created":
            raise RuntimeError("boom")
        real(session, event_type, **kw)

    monkeypatch.setattr(store, "_audit", failing)
    with pytest.raises(RuntimeError):
        edit(client, c, campaign(discount=25))
    monkeypatch.undo()
    after = client.get(f"{URL}/{c['campaign_id']}").json()
    assert after["current_version"] == 1
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(CampaignVersion)) == 1


# -------------------------------------------------------------------- audit


def test_audit_payloads_hold_no_secrets(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, sessions: sessionmaker[Session]
) -> None:
    from app.config import get_settings

    secret = "AIzaFAKE-should-never-be-stored-0123456789"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    get_settings.cache_clear()
    c = create(client, campaign_a())
    decide(client, c)
    decide(client, c, input_hash="0" * 64)
    with sessions() as s:
        dump = json.dumps([e.payload for e in s.scalars(select(AuditEvent))])
    assert secret not in dump
    allowed = {
        "campaign_type",
        "data_source",
        "is_synthetic",
        "version",
        "input_hash",
        "assumption_source",
        "trigger",
        "economics_status",
        "permitted",
        "executable",
        "blocked_by",
        "policy_version",
        "risk_scenario",
        "reason",
        "execution_status",
        "attempted_decision",
        "recommendation_run_id",
        "error_code",
        "previous_version",
        "change_note",
        "reasons",
    }
    with sessions() as s:
        for e in s.scalars(select(AuditEvent)):
            assert set(e.payload) <= allowed, (e.event_type, set(e.payload) - allowed)
