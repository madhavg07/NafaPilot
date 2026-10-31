"""Data validation route: check order rows before they are used."""

from __future__ import annotations

from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.data import DataRepository, get_repository
from app.schemas.analytics import ValidationReport
from app.services.analytics import validate_orders

router = APIRouter(prefix="/data", tags=["data"])

MAX_ROWS = 50_000


class ValidateRequest(BaseModel):
    orders: list[dict[str, Any]] = Field(max_length=MAX_ROWS)
    check_known_merchants: bool = True


@router.post("/validate", response_model=ValidationReport)
def validate(
    body: ValidateRequest, repo: Annotated[DataRepository, Depends(get_repository)]
) -> ValidationReport:
    """Validate order rows. Nothing is stored; the report says what would be rejected."""
    known = repo.merchant_ids if body.check_known_merchants else None
    _, report = validate_orders(pd.DataFrame(body.orders), known)
    return report
