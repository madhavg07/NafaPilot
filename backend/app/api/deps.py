"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.data import DataRepository, get_repository
from app.db import get_session
from app.services.merchant_settings import make_lookup
from app.services.promotions.selection import make_selection_lookup


def get_effective_repository(
    repo: Annotated[DataRepository, Depends(get_repository)],
    session: Annotated[Session, Depends(get_session)],
) -> DataRepository:
    """The dataset plus each merchant's saved settings (limits, costs) and product selection."""
    bind = session.get_bind()
    assert isinstance(bind, Engine)
    return repo.with_settings(make_lookup(bind), make_selection_lookup(bind))


EffectiveRepo = Annotated[DataRepository, Depends(get_effective_repository)]
