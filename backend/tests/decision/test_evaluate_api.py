from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_effective_repository
from app.data import DataRepository, get_repository
from app.main import app
from app.synthetic import generate
from tests.decision.fixtures import campaign_a, campaign_b


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    repo = DataRepository.from_tables(generate(42, include_test_merchants=True).tables)
    app.dependency_overrides[get_repository] = lambda: repo
    # No database here: use the dataset policy directly (settings are tested in tests/db).
    app.dependency_overrides[get_effective_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client: TestClient, inp: object) -> dict:  # type: ignore[type-arg]
    body = {"merchant_id": "M001", "campaign": inp.model_dump(mode="json")}  # type: ignore[attr-defined]
    resp = client.post("/api/v1/campaigns/evaluate", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


def test_evaluate_a_and_b_with_m001_policy(client: TestClient) -> None:
    a = post(client, campaign_a())
    b = post(client, campaign_b())
    assert [s["incremental_contribution_profit_inr"] for s in a["economics"]["scenarios"]] == [
        100.0,
        2400.0,
        4700.0,
    ]
    assert a["policy"]["permitted"] is True and a["policy"]["executable"] is False
    assert b["policy"]["blocked_by"] == ["max_loss"]
    assert a["is_synthetic"] is True and a["execution_mode"] == "not_executed"
    assert a["economics"]["result_kind"] == "prediction"


def test_evaluate_unknown_merchant(client: TestClient) -> None:
    body = {"merchant_id": "M999", "campaign": campaign_a().model_dump(mode="json")}
    assert client.post("/api/v1/campaigns/evaluate", json=body).status_code == 404
