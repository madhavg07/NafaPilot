"""Merchant settings edited in the app: limits, usual campaign costs and preferences."""

from __future__ import annotations

from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditEvent, MerchantSetting
from tests.db.conftest import alembic_config
from tests.db.test_campaigns import URL, create
from tests.decision.fixtures import campaign_a

S = "/api/v1/merchants/M001/settings"
BASE: dict[str, Any] = {
    "discount_cap_pct": "15",
    "min_margin_pct": "25",
    "max_loss_inr": "1000",
    "risk_scenario": "conservative",
    "budget_inr": "5000",
    "holdout_pct": 20,
    "campaign_costs_inr": {"targeted_offer": "1000", "off_peak": "1200", "new_customer": "800"},
}


def save(client: TestClient, **changes: Any) -> Any:
    return client.put(S, json={**BASE, **changes})


def test_defaults_come_from_the_dataset(client: TestClient) -> None:
    body = client.get(S).json()
    assert body["source"] == "default"
    assert body["policy"] == body["default_policy"]
    assert body["policy"]["policy_version"] == 1 and body["policy"]["max_loss_inr"] == 1000
    assert body["campaign_costs_inr"] == {
        "targeted_offer": 1000,
        "off_peak": 1200,
        "new_customer": 800,
        "product_offer": 500,
        "bundle_offer": 600,
        "cart_offer": 800,
    }
    assert body["preferences"] == {} and body["updated_at"] is None
    assert client.get("/api/v1/merchants/M001").json()["settings_source"] == "default"


def test_saving_identical_defaults_creates_a_version_once(client: TestClient) -> None:
    first = save(client).json()
    # The values equal the defaults, so nothing changes and no version is created.
    assert first["changed"] is False and first["source"] == "default"


def test_new_limits_create_a_version_used_by_new_evaluations(client: TestClient) -> None:
    before = create(client, campaign_a())  # ₹20 off on ₹200 = 10%, fine under 15%
    assert before["current"]["evaluation"]["policy"]["policy_version"] == 1

    r = save(client, discount_cap_pct="8", note="Smaller discounts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["source"] == "merchant"
    assert body["policy"]["policy_version"] == 2 and body["policy"]["discount_cap_pct"] == 8
    assert body["default_policy"]["discount_cap_pct"] == 15  # dataset default kept for reset

    after = create(client, campaign_a())
    pol = after["current"]["evaluation"]["policy"]
    assert pol["policy_version"] == 2 and "discount_cap" in pol["blocked_by"]
    # The earlier campaign's stored evaluation is unchanged.
    old = client.get(f"{URL}/{before['campaign_id']}").json()
    assert old["current"]["evaluation"]["policy"]["policy_version"] == 1

    profile = client.get("/api/v1/merchants/M001").json()
    assert profile["settings_source"] == "merchant" and profile["policy"]["discount_cap_pct"] == 8


def test_campaign_costs_are_saved_and_can_be_skipped(client: TestClient) -> None:
    body = save(
        client,
        campaign_costs_inr={"targeted_offer": "750", "off_peak": None, "new_customer": "800"},
    ).json()
    assert body["policy"]["policy_version"] == 2
    assert body["campaign_costs_inr"] == {
        "targeted_offer": 750,
        "off_peak": None,
        "new_customer": 800,
        "product_offer": 500,
        "bundle_offer": 600,
        "cart_offer": 800,
    }
    again = save(
        client,
        campaign_costs_inr={"targeted_offer": "750", "off_peak": None, "new_customer": "800"},
    ).json()
    assert again["changed"] is False and again["policy"]["policy_version"] == 2


def test_preferences_set_and_clear(client: TestClient) -> None:
    r = save(client, preferences={"max_discount_inr": 15, "target": "new_customers"}).json()
    assert r["preferences"] == {"max_discount_inr": "15", "target": "new_customers"}
    assert r["policy"]["policy_version"] == 1  # preferences alone don't change the policy
    prefs = client.get("/api/v1/merchants/M001/preferences").json()["preferences"]
    assert set(prefs) == {"max_discount_inr", "target"}

    cleared = save(client, preferences={"max_discount_inr": 15}).json()
    assert cleared["preferences"] == {"max_discount_inr": "15"}
    run = client.post(
        "/api/v1/merchants/M001/recommendations",
        json={"campaign_costs_inr": {"targeted_offer": 1000, "off_peak": 1200}},
    ).json()
    assert run["learning"]["preferences_applied"] == {"max_discount_inr": "15"}
    assert all(
        c["status"] == "filtered_by_preference" for c in run["candidates"] if c["discount_inr"] > 15
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"holdout_pct": 60},
        {"holdout_pct": 2},
        {"discount_cap_pct": "0"},
        {"discount_cap_pct": "70"},
        {"max_loss_inr": "-1"},
        {"risk_scenario": "worst"},
        {"campaign_costs_inr": {"sms_blast": "100"}},
        {"campaign_costs_inr": {"targeted_offer": "-5"}},
        {"preferences": {"target": "teenagers"}},
    ],
)
def test_invalid_settings_are_refused(client: TestClient, bad: dict[str, Any]) -> None:
    assert save(client, **bad).status_code == 422


def test_unknown_merchant(client: TestClient) -> None:
    assert client.get("/api/v1/merchants/M999/settings").status_code == 404
    assert client.put("/api/v1/merchants/M999/settings", json=BASE).status_code == 404


def test_audit_and_append_only(client: TestClient, sessions: sessionmaker[Session]) -> None:
    save(client, budget_inr="4000", preferences={"avoid_campaign_type": "off_peak"})
    with sessions() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.event_type == "settings_updated")).one()
        assert ev.payload["changed_limits"] == ["budget_inr"]
        assert ev.payload["changed_preferences"] == {"avoid_campaign_type": "off_peak"}
        assert s.scalar(select(func.count()).select_from(MerchantSetting)) == 1
    with sessions() as s, pytest.raises(DBAPIError, match="append-only"), s.begin():
        s.execute(text("UPDATE merchant_settings SET budget_inr = '1'"))


def test_downgrade_refuses_to_drop_saved_settings(
    client: TestClient, db_url: str, engine: Engine
) -> None:
    save(client, budget_inr="4000")
    engine.dispose()
    with pytest.raises(RuntimeError, match="saved setting"):
        command.downgrade(alembic_config(db_url), "0003_simulated_execution")
