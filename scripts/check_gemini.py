"""Gemini connectivity check. Run from the backend folder with the venv active:

    python ../scripts/check_gemini.py               # ping + sample candidates + sample explanation
    python ../scripts/check_gemini.py --list-models # list models your key can use

Reads GEMINI_API_KEY and GEMINI_MODEL from the project's .env. Never prints the key.
Exit code 0 = all checks passed, 1 = a check failed, 2 = not configured.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.schemas.llm import (  # noqa: E402
    CampaignType,
    CandidateContext,
    ExplainRecord,
    ExplanationRequest,
    PolicyLimits,
    ScenarioProfits,
)
from app.services.llm.errors import LLMError, LLMNotConfigured  # noqa: E402
from app.services.llm.gemini import GeminiProvider, SdkTransport  # noqa: E402

HINTS = {
    "llm_not_configured": "Set GEMINI_API_KEY and GEMINI_MODEL in the project-root .env file.",
    "llm_auth_or_request_error": "Check the key, and that GEMINI_MODEL appears in --list-models.",
    "llm_quota_exceeded": "Rate limit or quota reached. Wait a minute, or check it in AI Studio.",
    "llm_timeout": "Network slow or blocked. Retry, or raise LLM_TIMEOUT_SECONDS in .env.",
    "llm_upstream_error": "Gemini or the network had a problem. Retry shortly.",
    "llm_invalid_response": "The model's JSON did not match our schema. Retry or try a model.",
    "llm_safety_blocked": "Safety filters blocked the sample prompt.",
}

SAMPLE_CONTEXT = CandidateContext(
    merchant_id="M001",
    merchant_name="Anand Cafe (synthetic)",
    average_order_value_inr=Decimal("200"),
    variable_cost_per_order_inr=Decimal("110"),
    observed_facts=["Weekday 2-5 pm orders fell from 38 to 30 per day over 8 weeks (-21%)."],
    policy=PolicyLimits(discount_cap_pct=Decimal("15"), max_loss_inr=Decimal("1000")),
)

SAMPLE_EXPLAIN = ExplanationRequest(
    merchant_name="Anand Cafe (synthetic)",
    lang="hinglish",
    records=[
        ExplainRecord(
            record_id="A",
            campaign_type=CampaignType.TARGETED_OFFER,
            discount_inr=20,
            scenario_profits_inr=ScenarioProfits(
                conservative=Decimal("100"), expected=Decimal("2400"), optimistic=Decimal("4700")
            ),
            break_even_orders=26,
            policy_passed=True,
            selected=True,
        ),
        ExplainRecord(
            record_id="B",
            campaign_type=CampaignType.OFF_PEAK,
            discount_inr=30,
            scenario_profits_inr=ScenarioProfits(
                conservative=Decimal("-3600"), expected=Decimal("1200"), optimistic=Decimal("5400")
            ),
            break_even_orders=100,
            policy_passed=False,
            policy_reasons=["Conservative loss ₹3,600 exceeds the ₹1,000 limit."],
        ),
    ],
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()
    s = get_settings()
    print(f"provider setting : {s.llm_provider}")
    print(f"model            : {s.gemini_model or '(not set)'}")
    print(f"key present      : {'yes' if s.gemini_key_value else 'no'}")

    try:
        if args.list_models:
            if not s.gemini_key_value:
                raise LLMNotConfigured("GEMINI_API_KEY is not set.")
            names = SdkTransport(s.gemini_key_value, s.llm_timeout_seconds).list_models()
            print("\n".join(n for n in names if "gemini" in n))
            return 0
        provider = GeminiProvider(
            api_key=s.gemini_key_value,
            model=s.gemini_model,
            timeout_seconds=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
            temperature=s.llm_temperature,
            max_output_tokens=s.llm_max_output_tokens,
        )
        provider.ping()
        print("ping             : ok")
        cands = provider.generate_candidates(SAMPLE_CONTEXT)
        print(f"candidates       : ok ({len(cands.candidates)} valid)")
        for c in cands.candidates:
            kind, target = c.campaign_type.value, c.target_group.value
            print(f"  - {kind}: ₹{c.discount_inr} off, target {target}")
        exp = provider.explain(SAMPLE_EXPLAIN)
        print("explanation      : ok\n" + exp.text)
        return 0
    except LLMError as err:
        print(f"FAILED           : {err.error_code}: {err.message}")
        print(f"hint             : {HINTS.get(err.error_code, '')}")
        return 2 if isinstance(err, LLMNotConfigured) else 1
    except Exception as exc:  # list_models errors come straight from the SDK
        print(f"FAILED           : {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
