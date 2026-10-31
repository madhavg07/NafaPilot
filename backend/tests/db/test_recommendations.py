"""M7: recommendation runs, feedback loop, preferences and grounded explanations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.recommendations import provider_dependency
from app.db.models import Campaign, Explanation, RecommendationRun
from app.main import app
from app.schemas.llm import CampaignType, CandidateContext, CandidateSet
from app.services.llm.errors import LLMQuotaExceeded
from app.services.llm.gemini import GeminiProvider, RawResponse
from app.services.llm.mock import MockProvider
from tests.db.conftest import alembic_config
from tests.llm.fixtures import FAKE_KEY, VALID_CANDIDATES_JSON, FakeTransport

COSTS = {"targeted_offer": 1000, "off_peak": 1200, "new_customer": 1000}


@pytest.fixture
def use_provider() -> Iterator[Any]:
    def set_provider(provider: Any) -> None:
        app.dependency_overrides[provider_dependency] = lambda: provider

    yield set_provider
    app.dependency_overrides.pop(provider_dependency, None)


def generate(client: TestClient, merchant: str = "M001", **body: Any) -> Any:
    payload = {"campaign_costs_inr": COSTS, **body}
    return client.post(f"/api/v1/merchants/{merchant}/recommendations", json=payload)


def run_ok(client: TestClient, merchant: str = "M001", **body: Any) -> dict[str, Any]:
    resp = generate(client, merchant, **body)
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def cand(run: dict[str, Any], ctype: str) -> dict[str, Any]:
    return next(c for c in run["candidates"] if c["campaign_type"] == ctype)


def reject(client: TestClient, run: dict[str, Any], record: str, **extra: Any) -> Any:
    c = next(x for x in run["candidates"] if x["record_id"] == record)
    return client.post(
        f"/api/v1/campaigns/{c['campaign_id']}/versions/1/decision",
        json={"decision": "rejected", "input_hash": c["input_hash"], **extra},
    )


# ------------------------------------------------------------------ the deck story


def test_m001_run_reproduces_deck_decision(client: TestClient) -> None:
    run = run_ok(client)
    a, b = cand(run, "targeted_offer"), cand(run, "off_peak")
    assert [s["incremental_contribution_profit_inr"] for s in a["economics"]["scenarios"]] == [
        100.0,
        2400.0,
        4700.0,
    ]
    assert a["policy"]["permitted"] is True
    assert b["policy"]["blocked_by"] == ["max_loss"]
    sel = run["selection"]
    assert sel["record_id"] == a["record_id"] and sel["campaign_id"] == a["campaign_id"]
    assert "not by the model" in run["notice"]
    assert run["execution_status"] == "not_executed" and run["is_synthetic"] is True

    # Context came from validated analytics, including the deck's -21% alert.
    facts = run["context"]["llm_context"]["observed_facts"]
    assert any("38.0 to 30.0" in f and "Cause unknown" in f for f in facts)
    assert run["context"]["settings_source"] == "merchant_entered_settings"

    exp = run["explanations"][0]
    assert exp["source"] == "llm" and exp["grounded"] is True and exp["provider"] == "mock"
    assert "2400.00" in exp["text"]

    learning = run["learning"]
    assert learning["model_retrained"] is False
    assert "milestone 8" in learning["performance_learning"]

    # Both evaluated options are persisted campaigns (version 1), pending approval.
    for c in (a, b):
        stored = client.get(f"/api/v1/campaigns/{c['campaign_id']}").json()
        assert stored["approval_status"] == "pending"
        assert stored["current"]["inputs"]["assumption_source"] == "llm_suggestion"
        assert stored["current"]["input_hash"] == c["input_hash"]


def test_rejection_with_preference_changes_the_next_run(client: TestClient) -> None:
    first = run_ok(client)
    a = cand(first, "targeted_offer")
    resp = reject(
        client,
        first,
        a["record_id"],
        reason="I don't want to give discounts to existing customers.",
        preference={"key": "target", "value": "new_customers"},
    )
    assert resp.status_code == 200, resp.text
    prefs = client.get("/api/v1/merchants/M001/preferences").json()["preferences"]
    assert prefs["target"]["value"] == "new_customers"

    second = run_ok(client)
    assert [c["campaign_type"] for c in second["candidates"]] == ["new_customer"]
    new = second["candidates"][0]
    assert new["target_group"] == "new_customers" and new["policy"]["permitted"] is True
    assert second["selection"]["campaign_type"] == "new_customer"
    assert second["learning"]["preferences_applied"] == {"target": "new_customers"}
    assert second["learning"]["past_decisions_used"] == 1
    history = second["context"]["llm_context"]["feedback_history"]
    assert history[0]["decision"] == "rejected" and "existing customers" in history[0]["reason"]


def test_free_text_reason_is_history_not_a_preference(client: TestClient) -> None:
    first = run_ok(client)
    reject(
        client,
        first,
        cand(first, "targeted_offer")["record_id"],
        reason="Only new customers please",
    )
    assert client.get("/api/v1/merchants/M001/preferences").json()["preferences"] == {}
    second = run_ok(client)
    assert second["learning"]["preferences_applied"] == {}
    assert second["learning"]["past_decisions_used"] == 1


class _IgnoresPreferences(MockProvider):
    """An LLM that ignores the merchant's preferences and allowed types entirely."""

    def generate_candidates(self, ctx: CandidateContext) -> CandidateSet:
        ignored = ctx.model_copy(
            update={"preferences": {}, "allowed_campaign_types": list(CampaignType)}
        )
        return super().generate_candidates(ignored)


def test_code_enforces_preferences_even_if_the_llm_ignores_them(
    client: TestClient, use_provider: Any
) -> None:
    first = run_ok(client)
    reject(
        client,
        first,
        cand(first, "targeted_offer")["record_id"],
        preference={"key": "avoid_campaign_type", "value": "targeted_offer"},
    )
    use_provider(_IgnoresPreferences())
    run = run_ok(client)
    a = cand(run, "targeted_offer")
    assert a["status"] == "filtered_by_preference" and a["campaign_id"] is None
    assert "no targeted_offer campaigns" in a["filter_reasons"][0]
    # Only B remains, and policy blocks it, so the recommendation is to do nothing.
    assert run["selection"]["campaign_type"] == "no_action"


# --------------------------------------------------------------- missing inputs


def test_missing_campaign_cost_is_not_invented(client: TestClient) -> None:
    run = run_ok(client, campaign_costs_inr={"off_peak": 1200})
    a = cand(run, "targeted_offer")
    assert a["cost_source"] == "missing"
    assert a["economics"]["status"] == "insufficient_data"
    assert run["selection"]["campaign_type"] == "no_action"


def test_merchant_without_cost_data_gets_no_recommendation(client: TestClient) -> None:
    run = run_ok(client, "M007")
    assert all(c["economics"]["status"] == "insufficient_data" for c in run["candidates"])
    assert run["selection"]["campaign_type"] == "no_action"
    exp = run["explanations"][0]
    assert exp["source"] == "template" and exp["fallback_reason"] == "no_evaluable_candidates"
    assert "variable_cost_per_order_inr" in exp["text"]


def test_bad_requests(client: TestClient) -> None:
    assert generate(client, "M999").status_code == 404
    assert generate(client, campaign_costs_inr={"off_peak": -1}).status_code == 422
    assert generate(client, campaign_costs_inr={"no_action": 5}).status_code == 422


def test_preference_validation(client: TestClient) -> None:
    run = run_ok(client)
    bad_value = reject(client, run, "A", preference={"key": "target", "value": "everyone"})
    assert bad_value.status_code == 422
    a = cand(run, "targeted_offer")
    on_approval = client.post(
        f"/api/v1/campaigns/{a['campaign_id']}/versions/1/decision",
        json={
            "decision": "approved",
            "input_hash": a["input_hash"],
            "preference": {"key": "target", "value": "new_customers"},
        },
    )
    assert on_approval.status_code == 422


# ------------------------------------------------------------------ explanations


def _gemini(*steps: Any) -> GeminiProvider:
    return GeminiProvider(
        api_key=FAKE_KEY,
        model="gemini-test",
        max_retries=0,
        transport=FakeTransport(*steps),
        sleep=lambda _s: None,
    )


def _explanation(text_: str, ids: list[str]) -> RawResponse:
    return RawResponse(text=json.dumps({"text": text_, "lang": "en", "referenced_record_ids": ids}))


def test_grounded_llm_explanation_is_kept(client: TestClient, use_provider: Any) -> None:
    use_provider(
        _gemini(
            RawResponse(text=VALID_CANDIDATES_JSON),
            _explanation("Option A: expected ₹2,400, worst case ₹100. Estimates only.", ["A"]),
        )
    )
    exp = run_ok(client, campaign_costs_inr={"targeted_offer": 1000})["explanations"][0]
    assert exp["source"] == "llm" and exp["provider"] == "gemini" and exp["grounded"]


def test_ungrounded_llm_explanation_falls_back_to_template(
    client: TestClient, use_provider: Any
) -> None:
    use_provider(
        _gemini(
            RawResponse(text=VALID_CANDIDATES_JSON),
            _explanation("Option A will earn ₹2,500 and grow sales 15%.", ["A"]),
        )
    )
    exp = run_ok(client, campaign_costs_inr={"targeted_offer": 1000})["explanations"][0]
    assert exp["source"] == "template" and exp["grounded"] is True
    assert exp["fallback_reason"] == "ungrounded_numbers: 15, 2500"
    assert "2500" not in exp["text"]


def test_explain_failure_falls_back_but_generation_failure_writes_nothing(
    client: TestClient, use_provider: Any, sessions: sessionmaker[Session]
) -> None:
    use_provider(MockProvider(fail_with=LLMQuotaExceeded("quota")))
    resp = generate(client)
    assert resp.status_code == 429 and resp.json()["error_code"] == "llm_quota_exceeded"
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(RecommendationRun)) == 0
        assert s.scalar(select(func.count()).select_from(Campaign)) == 0


def test_hinglish_explanation_and_question(client: TestClient) -> None:
    run = run_ok(client)
    resp = client.post(
        f"/api/v1/recommendations/{run['run_id']}/explanations",
        json={"lang": "hinglish", "question": "Off-peak kyun nahi?"},
    )
    assert resp.status_code == 201, resp.text
    exp = resp.json()
    assert exp["lang"] == "hinglish" and exp["question"] == "Off-peak kyun nahi?"
    assert exp["grounded"] is True and "policy ne block kiya" in exp["text"]
    stored = client.get(f"/api/v1/recommendations/{run['run_id']}").json()
    assert len(stored["explanations"]) == 2
    assert (
        client.post(
            "/api/v1/recommendations/00000000-0000-0000-0000-000000000000/explanations", json={}
        ).status_code
        == 404
    )


def test_unanswered_question_is_labelled_not_disguised(
    client: TestClient, use_provider: Any
) -> None:
    """If the LLM can't answer, the stored template says so instead of looking like an answer."""
    from tests.llm.test_gemini_provider import client_error

    use_provider(
        _gemini(
            RawResponse(text=VALID_CANDIDATES_JSON),
            _explanation("Option A: expected ₹2,400, worst case ₹100. Estimates only.", ["A"]),
            client_error(429, "RESOURCE_EXHAUSTED"),
        )
    )
    run = run_ok(client, campaign_costs_inr={"targeted_offer": 1000})
    resp = client.post(
        f"/api/v1/recommendations/{run['run_id']}/explanations",
        json={"lang": "en", "question": "Any other offer?"},
    )
    assert resp.status_code == 201, resp.text
    exp = resp.json()
    assert exp["source"] == "template" and exp["fallback_reason"] == "llm_quota_exceeded"
    assert exp["text"].splitlines()[0].startswith("The AI service is at its usage limit")
    assert "not answered" in exp["text"]


def test_audit_records_run_and_explanations(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    run = run_ok(client)
    with sessions() as s:
        rows = (
            s.execute(
                text("SELECT event_type FROM audit_events WHERE campaign_id IS NULL ORDER BY id")
            )
            .scalars()
            .all()
        )
    assert rows == ["explanation_generated", "recommendation_generated"]
    a = cand(run, "targeted_offer")
    events = [
        e["event_type"] for e in client.get(f"/api/v1/campaigns/{a['campaign_id']}/audit").json()
    ]
    assert events[0] == "campaign_created"


# ---------------------------------------------------------------- database rules


def test_new_tables_are_append_only(client: TestClient, sessions: sessionmaker[Session]) -> None:
    run_ok(client)
    for table in ("explanations", "recommendation_runs"):
        with sessions() as s, pytest.raises(DBAPIError, match="append-only"), s.begin():
            s.execute(text(f"DELETE FROM {table}"))


def test_llm_text_must_be_grounded_in_database(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    run = run_ok(client)
    with sessions() as s, pytest.raises(IntegrityError), s.begin():
        s.add(
            Explanation(
                recommendation_run_id=uuid.UUID(run["run_id"]),
                lang="en",
                source="llm",
                provider="x",
                model="x",
                grounded=False,
                text="t",
                referenced_records=[],
            )
        )
        s.flush()


def test_migration_0002_preserves_existing_campaigns(db_url: str, engine: Any) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "0001_campaign_persistence")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO campaigns (id, merchant_id, campaign_type, current_version, "
                "execution_status, data_source, is_synthetic, created_by) VALUES "
                "('0f0e0d0c-0b0a-0908-0706-050403020100', 'M001', 'targeted_offer', 1, "
                "'not_executed', 'x', true, 't')"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT merchant_id, recommendation_run_id FROM campaigns")
        ).all() == [("M001", None)]
    command.downgrade(cfg, "0001_campaign_persistence")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT merchant_id FROM campaigns")).scalars().all() == ["M001"]
    command.upgrade(cfg, "head")
