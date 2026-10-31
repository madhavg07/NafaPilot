import pytest

from app.data import DataRepository
from app.schemas.analytics import MerchantAnalysis
from app.services.analytics import analyze_merchant
from app.synthetic import Dataset, generate


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return generate(42, include_test_merchants=True)


@pytest.fixture(scope="session")
def repo(dataset: Dataset) -> DataRepository:
    return DataRepository.from_tables(dataset.tables)


@pytest.fixture(scope="session")
def analyses(repo: DataRepository) -> dict[str, MerchantAnalysis]:
    return {
        mid: analyze_merchant(
            repo.orders_for(mid),
            repo.merchant_info(mid),
            repo.as_of,
            data_source=repo.data_source,
            is_synthetic=True,
            known_merchants=repo.merchant_ids,
        )
        for mid in sorted(repo.merchant_ids)
    }
