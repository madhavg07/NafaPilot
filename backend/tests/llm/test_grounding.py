from decimal import Decimal

from app.services.llm.grounding import numbers_in, ungrounded_numbers
from app.services.llm.templates import template_explanation
from tests.llm.fixtures import explain_request


def test_numbers_are_compared_by_value() -> None:
    assert numbers_in("₹2,400 or 2400.00 or −₹3,600") == {Decimal(2400), Decimal(3600)}


def test_ungrounded_numbers_detected() -> None:
    source = '{"expected": "2400.00", "conservative": "-3600"}'
    assert ungrounded_numbers("Expected ₹2,400; worst case −₹3,600.", source) == []
    assert ungrounded_numbers("Expected ₹2,500, about 12% better.", source) == ["12", "2500"]


def test_template_is_always_grounded() -> None:
    for lang in ("en", "hinglish"):
        req = explain_request(lang)
        text = template_explanation(req).text
        assert ungrounded_numbers(text, req.model_dump_json()) == []


def test_template_says_when_a_question_was_not_answered() -> None:
    for lang in ("en", "hinglish"):
        req = explain_request(lang).model_copy(update={"question": "Why not the off-peak offer?"})
        quota = template_explanation(req, "llm_quota_exceeded").text
        first = quota.splitlines()[0].lower()
        assert ("not answered" in first) or ("jawab nahi" in first)
        assert ungrounded_numbers(quota, req.model_dump_json()) == []
        # Without a question there is no "not answered" line.
        plain = template_explanation(explain_request(lang), "llm_quota_exceeded").text
        assert "not answered" not in plain.lower() and "jawab nahi" not in plain.lower()
        # Ungrounded and unknown reasons also say so; the mock (no reason) says it can't answer.
        assert (
            "discarded" in template_explanation(req, "ungrounded_numbers: 12").text
            or lang == "hinglish"
        )
        assert template_explanation(req).text.splitlines()[0] != quota.splitlines()[0]
