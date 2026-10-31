import pytest

from app.schemas.llm import CampaignType, TargetGroup
from app.services.llm.errors import LLMQuotaExceeded
from app.services.llm.mock import MockProvider
from tests.llm.fixtures import anand_context, explain_request


def test_mock_is_deterministic() -> None:
    p = MockProvider()
    assert p.generate_candidates(anand_context()) == p.generate_candidates(anand_context())


def test_mock_reproduces_deck_candidates() -> None:
    cands = MockProvider().generate_candidates(anand_context()).candidates
    by_type = {c.campaign_type: c for c in cands}
    assert by_type[CampaignType.TARGETED_OFFER].discount_inr == 20  # 10% of ₹200
    assert by_type[CampaignType.OFF_PEAK].discount_inr == 30  # 15% cap of ₹200
    assert by_type[CampaignType.TARGETED_OFFER].assumptions.expected.incremental_orders == 60
    assert CampaignType.NO_ACTION in by_type


def test_mock_respects_new_customer_preference() -> None:
    ctx = anand_context(preferences={"target": "new_customers"})
    cands = MockProvider().generate_candidates(ctx).candidates
    types = {c.campaign_type for c in cands}
    assert CampaignType.TARGETED_OFFER not in types
    assert CampaignType.OFF_PEAK not in types
    new = next(c for c in cands if c.campaign_type is CampaignType.NEW_CUSTOMER)
    assert new.target_group is TargetGroup.NEW_CUSTOMERS


def test_mock_respects_allowed_types() -> None:
    ctx = anand_context(allowed_campaign_types=[CampaignType.NO_ACTION])
    cands = MockProvider().generate_candidates(ctx).candidates
    assert [c.campaign_type for c in cands] == [CampaignType.NO_ACTION]


@pytest.mark.parametrize("lang", ["en", "hinglish"])
def test_mock_explanation_references_all_records(lang: str) -> None:
    req = explain_request(lang)
    exp = MockProvider().explain(req)
    assert exp.lang == lang
    assert exp.referenced_record_ids == ["A", "B"]
    assert "2400" in exp.text and "-3600" in exp.text


def test_mock_failure_injection() -> None:
    with pytest.raises(LLMQuotaExceeded):
        MockProvider(fail_with=LLMQuotaExceeded("quota")).ping()
