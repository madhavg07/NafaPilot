"""Data access layer (CSV now, PostgreSQL from milestone 6)."""

from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.data.repository import DataRepository, DatasetMissing, UnknownMerchant

__all__ = ["DataRepository", "DatasetMissing", "UnknownMerchant", "get_repository"]


@lru_cache
def _load(path: str) -> DataRepository:
    return DataRepository.from_dir(Path(path))


def get_repository() -> DataRepository:
    """FastAPI dependency. Loaded once per process; tests override it."""
    return _load(str(get_settings().resolved_data_dir))
