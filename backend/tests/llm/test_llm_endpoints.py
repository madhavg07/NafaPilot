from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


def test_status_mock(client: TestClient) -> None:
    use(Settings(llm_provider="mock", gemini_api_key=""))
    body = client.get("/api/v1/llm/status").json()
    chain = body.pop("chain")
    assert body == {
        "provider": "mock",
        "model": "mock-deterministic-v1",
        "configured": True,
        "key_present": False,
    }
    assert [m["provider"] for m in chain] == ["mock"]


def test_status_gemini_without_key(client: TestClient) -> None:
    use(Settings(llm_provider="gemini", gemini_api_key="", gemini_model="gemini-x"))
    body = client.get("/api/v1/llm/status").json()
    assert body["configured"] is False and body["key_present"] is False


def test_ping_mock_ok(client: TestClient) -> None:
    use(Settings(llm_provider="mock", environment="development"))
    body = client.post("/api/v1/llm/ping").json()
    assert body["ok"] is True and body["provider"] == "mock"


def test_ping_gemini_not_configured_is_503(client: TestClient) -> None:
    use(Settings(llm_provider="gemini", gemini_api_key="", gemini_model="gemini-x"))
    resp = client.post("/api/v1/llm/ping")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False and body["error_code"] == "llm_not_configured"
    assert ".env" in body["message"]


def test_ping_disabled_in_production(client: TestClient) -> None:
    use(Settings(llm_provider="mock", environment="production"))
    assert client.post("/api/v1/llm/ping").status_code == 404
