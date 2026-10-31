import pandas as pd

from app.services.analytics import validate_orders
from app.synthetic import Dataset


def test_each_planted_defect_is_reported(dataset: Dataset) -> None:
    bad = dataset["invalid_orders"]
    _, report = validate_orders(bad, known_merchants={"M001"})
    found = {i.code for i in report.issues}
    for issue in bad["expected_issue"]:
        assert issue in found, issue


def test_errors_reject_rows_warnings_keep_them(dataset: Dataset) -> None:
    bad = dataset["invalid_orders"]
    clean, report = validate_orders(bad, known_merchants={"M001"})
    assert list(clean["order_id"]) == ["BAD-006"]  # cost_exceeds_value: warning only
    by_code = {i.code: i for i in report.issues}
    assert by_code["cost_exceeds_value"].severity == "warning"
    assert by_code["duplicate_order_id"].count == 2
    assert report.rows_valid + report.rows_rejected == report.rows_received


def test_clean_synthetic_orders_have_no_errors(dataset: Dataset) -> None:
    orders = dataset["orders"]
    clean, report = validate_orders(orders, set(dataset["merchants"]["merchant_id"]))
    assert not [i for i in report.issues if i.severity == "error"]
    assert len(clean) == len(orders)


def test_missing_required_columns() -> None:
    clean, report = validate_orders(pd.DataFrame({"order_id": ["a"]}))
    assert clean.empty
    assert report.issues[0].code == "missing_columns"
    assert "merchant_id" in report.issues[0].examples


def test_clean_rows_get_parsed_time_columns() -> None:
    df = pd.DataFrame(
        {
            "order_id": ["a"],
            "merchant_id": ["M001"],
            "order_ts": ["2026-10-19T15:30:00"],
            "order_value_inr": [200],
        }
    )
    clean, _ = validate_orders(df)
    row = clean.iloc[0]
    assert row["hour"] == 15 and row["weekday"] == 0  # Monday
    assert str(row["date"]) == "2026-10-19"


def _rows(**cols: list[object]) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    base: dict[str, list[object]] = {
        "order_id": [f"o{i}" for i in range(n)],
        "merchant_id": ["M001"] * n,
        "order_ts": ["2026-10-19T15:30:00"] * n,
        "order_value_inr": [200] * n,
    }
    base.update(cols)
    return pd.DataFrame(base)


def _codes(report: object) -> dict[str, tuple[str, int]]:
    return {i.code: (i.severity, i.count) for i in report.issues}  # type: ignore[attr-defined]


def test_missing_or_blank_order_id_rejected_not_duplicate() -> None:
    clean, report = validate_orders(_rows(order_id=[None, "", "   ", "ok"]))
    assert list(clean["order_id"]) == ["ok"]
    codes = _codes(report)
    assert codes["missing_order_id"] == ("error", 3)
    assert "duplicate_order_id" not in codes  # blanks are not duplicates of each other
    assert report.issues[0].examples[0] == "row 0"


def test_malformed_and_negative_cost_are_discarded_with_warning() -> None:
    clean, report = validate_orders(_rows(variable_cost_inr=["abc", -10, "", 110, "95.5"]))
    assert len(clean) == 5  # cost problems never reject an order
    costs = clean["variable_cost_inr"].tolist()
    assert pd.isna(costs[0]) and pd.isna(costs[1]) and pd.isna(costs[2])
    assert costs[3] == 110 and costs[4] == 95.5
    codes = _codes(report)
    assert codes["unparseable_variable_cost"] == ("warning", 1)
    assert codes["negative_variable_cost"] == ("warning", 1)
    assert codes["missing_variable_cost"] == ("warning", 1)  # only the genuinely blank one
    assert report.field_completeness["variable_cost_inr"] == 0.4


def test_supplied_hour_versus_timestamp() -> None:
    # Timestamp hour is 15 for every row.
    clean, report = validate_orders(_rows(hour=[15, 9, "x", 14.5, 30, None]))
    codes = _codes(report)
    assert codes["hour_mismatch"] == ("warning", 1)  # 9: kept, timestamp hour used
    assert codes["invalid_hour"] == ("error", 2)  # "x" and 14.5
    assert codes["hour_out_of_range"] == ("error", 1)  # 30
    assert list(clean["order_id"]) == ["o0", "o1", "o5"]  # blank hour is fine
    assert (clean["hour"] == 15).all()
