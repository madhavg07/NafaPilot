import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.llm import CandidateSet
from tests.llm.fixtures import VALID_CANDIDATES_JSON


def _payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(VALID_CANDIDATES_JSON)
    return data


def test_valid_candidates_parse() -> None:
    cs = CandidateSet.model_validate(_payload())
    assert cs.candidates[0].discount_inr == 20


def test_extra_field_rejected() -> None:
    data = _payload()
    data["candidates"][0]["expected_profit_inr"] = 2400  # LLM must not supply profit
    with pytest.raises(ValidationError):
        CandidateSet.model_validate(data)


def test_unknown_campaign_type_rejected() -> None:
    data = _payload()
    data["candidates"][0]["campaign_type"] = "free_money"
    with pytest.raises(ValidationError):
        CandidateSet.model_validate(data)


def test_negative_discount_rejected() -> None:
    data = _payload()
    data["candidates"][0]["discount_inr"] = -5
    with pytest.raises(ValidationError):
        CandidateSet.model_validate(data)


def test_scenarios_must_be_ordered() -> None:
    data = _payload()
    data["candidates"][0]["assumptions"]["conservative"]["incremental_orders"] = 500
    with pytest.raises(ValidationError, match="conservative <= expected <= optimistic"):
        CandidateSet.model_validate(data)


def test_no_action_must_be_zero() -> None:
    data = _payload()
    data["candidates"][1]["discount_inr"] = 10
    with pytest.raises(ValidationError, match="no_action"):
        CandidateSet.model_validate(data)


def test_empty_candidate_list_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateSet.model_validate({"candidates": []})
