"""Deterministic explanation template.

Used by the mock provider and as the fallback whenever an LLM explanation fails or is
not grounded. It only restates values that are in the request, so it always passes the
numeric grounding check. It cannot answer free-text questions; when one was asked, the
text says so first instead of looking like an answer.
"""

from __future__ import annotations

from app.schemas.llm import Explanation, ExplanationRequest

# Why the AI didn't answer, keyed by the fallback reason. No digits, so the text stays grounded.
_NOT_ANSWERED_EN = {
    "llm_quota_exceeded": "The AI service is at its usage limit right now, so your question was "
    "not answered. Try again in a minute.",
    "llm_timeout": "The AI service did not respond in time, so your question was not answered.",
    "llm_upstream_error": "The AI service had an error, so your question was not answered.",
    "ungrounded_numbers": "The AI's answer used numbers that are not in the calculations, so it "
    "was discarded and your question was not answered.",
}
_NOT_ANSWERED_HI = {
    "llm_quota_exceeded": "AI service abhi apni limit par hai, isliye aapke sawaal ka jawab nahi "
    "mila. Thodi der baad dobara try karein.",
    "llm_timeout": "AI service ne time par jawab nahi diya, isliye sawaal ka jawab nahi mila.",
    "llm_upstream_error": "AI service mein error aaya, isliye sawaal ka jawab nahi mila.",
    "ungrounded_numbers": "AI ke jawab mein aise numbers the jo calculation mein nahi hain, "
    "isliye use hata diya gaya.",
}


def _not_answered(request: ExplanationRequest, reason: str | None) -> str:
    hinglish = request.lang == "hinglish"
    key = (reason or "").split(":", 1)[0]
    table = _NOT_ANSWERED_HI if hinglish else _NOT_ANSWERED_EN
    if key in table:
        lead = table[key]
    elif reason:
        lead = (
            "AI is sawaal ka jawab nahi de saka."
            if hinglish
            else "The AI could not answer this question."
        )
    else:
        lead = (
            "Ye fixed summary free-text sawaalon ka jawab nahi deti."
            if hinglish
            else "This fixed summary does not answer free-text questions."
        )
    tail = (
        "Neeche recorded options ka summary hai:"
        if hinglish
        else "Here is the summary of the recorded options instead:"
    )
    return f"{lead} {tail}"


def template_explanation(request: ExplanationRequest, reason: str | None = None) -> Explanation:
    """`reason` is why the LLM was not used (an error code), if any."""
    hinglish = request.lang == "hinglish"
    lines: list[str] = []
    if request.question:
        lines.append(_not_answered(request, reason))
    for r in request.records:
        p = r.scenario_profits_inr
        tag = f"[{r.record_id}] " + (r.offer or f"{r.campaign_type} (₹{r.discount_inr} off)")
        if hinglish:
            status = "policy ke andar hai" if r.policy_passed else "policy ne block kiya"
            pick = " Yahi suggest kiya gaya hai." if r.selected else ""
            be = (
                f" Break-even ke liye {r.break_even_orders} extra orders chahiye."
                if r.break_even_orders is not None
                else ""
            )
            lines.append(
                f"{tag}: andaaza ₹{p.expected}, range ₹{p.conservative} se ₹{p.optimistic}; "
                f"{status}.{be}{pick}"
            )
        else:
            status = "passes policy" if r.policy_passed else "blocked by policy"
            pick = " This is the recommended option." if r.selected else ""
            be = (
                f" It needs {r.break_even_orders} extra orders to break even."
                if r.break_even_orders is not None
                else ""
            )
            lines.append(
                f"{tag}: expected ₹{p.expected}, range ₹{p.conservative} to ₹{p.optimistic}; "
                f"{status}.{be}{pick}"
            )
        if not r.policy_passed and r.policy_reasons:
            lines.append("  " + "; ".join(r.policy_reasons))
    if not any(r.selected for r in request.records):
        lines.append(
            "Inmein se koi option suggest nahi kiya gaya, isliye abhi kuch na karna behtar hai."
            if hinglish
            else "None of these options is recommended, so the recommendation is to do nothing "
            "for now."
        )
    lines.append(
        "Ye estimates hain, guarantee nahi. Data synthetic hai."
        if hinglish
        else "These are estimates under stated assumptions, not guarantees. Data is synthetic."
    )
    return Explanation(
        text="\n".join(lines),
        lang=request.lang,
        referenced_record_ids=[r.record_id for r in request.records],
    )
