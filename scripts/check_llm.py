"""AI provider check for Sarvam, Gemini or the configured fallback chain.

Run from the backend folder with the venv active:

    python ../scripts/check_llm.py                     # every provider in LLM_PROVIDER, one by one
    python ../scripts/check_llm.py --provider sarvam   # only Sarvam
    python ../scripts/check_llm.py --quick             # ping only (uses the fewest tokens)

Each check runs: ping, sample candidates, sample Hinglish explanation. Reads keys from the
project-root .env and never prints them. Exit code 0 = the first provider in the chain
works, 1 = it failed (the app will fall back to the next one), 2 = nothing configured.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_gemini import HINTS, SAMPLE_CONTEXT, SAMPLE_EXPLAIN  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.llm.errors import LLMError, LLMNotConfigured  # noqa: E402
from app.services.llm.factory import _build, llm_status  # noqa: E402

HINTS = {
    **HINTS,
    "llm_not_configured": "Set the provider's API key and model in the project-root .env file.",
    "llm_auth_or_request_error": "Check the key and the model name (SARVAM_MODEL / GEMINI_MODEL). "
    "If Sarvam rejects reasoning_effort, set SARVAM_REASONING_EFFORT= (empty).",
    "llm_quota_exceeded": "Rate limit or credit used up. Wait, or check your provider dashboard.",
    "llm_upstream_error": "The provider or the network had a problem. Retry shortly.",
    "llm_invalid_response": "The answer did not match our JSON format, or was cut off. "
    "Raise LLM_MAX_OUTPUT_TOKENS (up to 8192), or try another model.",
}


def check(name: str, quick: bool) -> bool:
    s = get_settings()
    print(f"\n== {name} ==")
    try:
        provider = _build(name, s)
        print(f"model       : {provider.model}")
        provider.ping()
        print("ping        : ok")
        if quick:
            return True
        cands = provider.generate_candidates(SAMPLE_CONTEXT)
        print(f"candidates  : ok ({len(cands.candidates)} valid)")
        for c in cands.candidates:
            print(
                f"  - {c.campaign_type.value}: ₹{c.discount_inr} off, target {c.target_group.value}"
            )
        exp = provider.explain(SAMPLE_EXPLAIN)
        print("explanation : ok\n" + exp.text)
        return True
    except LLMError as err:
        print(f"FAILED      : {err.error_code}: {err.message}")
        print(f"hint        : {HINTS.get(err.error_code, '')}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["sarvam", "gemini", "mock"])
    parser.add_argument("--quick", action="store_true", help="ping only")
    args = parser.parse_args()
    s = get_settings()
    status = llm_status(s)
    print(f"LLM_PROVIDER : {s.llm_provider}  (tried in this order)")
    for m in status["chain"]:  # type: ignore[union-attr]
        print(
            f"  {m['provider']:<7} model={m['model'] or '(not set)'}  "
            f"key={'yes' if m['key_present'] else 'no'}  "
            f"configured={'yes' if m['configured'] else 'no'}"
        )
    names = [args.provider] if args.provider else s.llm_provider_chain
    results = {n: check(n, args.quick) for n in names}
    print()
    if not any(results.values()):
        print("Result: no provider works right now. Requests will fail until one does.")
        return 2 if all(not m["configured"] for m in status["chain"]) else 1  # type: ignore[union-attr]
    first = names[0]
    if results[first]:
        print(f"Result: {first} works. It will be used first.")
        return 0
    working = [n for n, ok in results.items() if ok]
    print(f"Result: {first} failed; the app will fall back to {working[0]}.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LLMNotConfigured as err:
        print(f"FAILED: {err.message}")
        raise SystemExit(2) from None
