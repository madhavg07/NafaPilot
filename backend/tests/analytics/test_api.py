from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_effective_repository
from app.data import DataRepository, get_repository
from app.main import app


@pytest.fixture
def client(repo: DataRepository) -> Iterator[TestClient]:
    app.dependency_overrides[get_repository] = lambda: repo
    # No database here: use the dataset policy directly (settings are tested in tests/db).
    app.dependency_overrides[get_effective_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_merchants(client: TestClient) -> None:
    body = client.get("/api/v1/merchants").json()
    assert body["is_synthetic"] is True
    assert len(body["merchants"]) == 8  # four shops + four test-only edge cases


def test_merchant_profile(client: TestClient) -> None:
    body = client.get("/api/v1/merchants/M001").json()
    assert body["name"] == "Anand Café"
    assert body["policy"]["max_loss_inr"] == 1000
    assert body["variable_cost_per_order_inr"] == 110


def test_baseline_endpoint(client: TestClient) -> None:
    resp = client.get("/api/v1/merchants/M001/baseline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_synthetic"] is True
    assert body["alerts"][0]["change_pct"] == pytest.approx(-21.05, abs=0.01)
    assert isinstance(body["baseline"]["revenue_inr"], float)  # money as JSON numbers


def test_unknown_merchant_404(client: TestClient) -> None:
    resp = client.get("/api/v1/merchants/M999/baseline")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "merchant_not_found"


def test_validate_endpoint(client: TestClient) -> None:
    rows = [
        {
            "order_id": "a",
            "merchant_id": "M001",
            "order_ts": "2026-10-01T10:00:00",
            "order_value_inr": 200,
        },
        {
            "order_id": "b",
            "merchant_id": "M999",
            "order_ts": "2026-10-01T10:00:00",
            "order_value_inr": 200,
        },
    ]
    body = client.post("/api/v1/data/validate", json={"orders": rows}).json()
    assert body["rows_valid"] == 1 and body["rows_rejected"] == 1
    assert body["issues"][0]["code"] == "unknown_merchant"


def test_missing_dataset_is_503(tmp_path: Path) -> None:
    def missing() -> DataRepository:
        return DataRepository.from_dir(tmp_path)

    app.dependency_overrides[get_repository] = missing
    try:
        resp = TestClient(app).get("/api/v1/merchants")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "dataset_missing"
    assert "generate_dataset.py" in resp.json()["message"]
