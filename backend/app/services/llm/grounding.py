"""Numeric grounding check for LLM explanations.

An explanation may only use numbers that appear in what it was given (the stored
engine records and observed facts). Any other number, such as a recalculated total,
a rounded figure or an invented percentage, makes it ungrounded, and the caller falls
back to the deterministic template.

Numbers are compared by value: "2,400", "2400" and "2400.00" match, and the sign is
ignored, so "−₹3,600" matches -3600. Record ids are letters so they add no digits.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for raw in _NUMBER.findall(text):
        try:
            found.add(Decimal(raw.replace(",", "")).normalize())
        except InvalidOperation:  # pragma: no cover - regex only matches numerals
            continue
    return found


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that do not appear anywhere in `source`, as plain strings."""
    allowed = numbers_in(source)
    return sorted(format(n, "f") for n in numbers_in(text) - allowed)
