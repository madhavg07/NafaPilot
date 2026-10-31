"""Order data validation. Pure function: DataFrame in, (clean rows, report) out.

Rule: errors reject a row when the order itself cannot be trusted (identity, merchant,
time or value). Warnings keep the row when only an optional or derived field is wrong;
the bad value is then not used (for example, an invalid cost is treated as missing).

Field notes:
- order_ts is the source of truth for time. A supplied `hour` that is impossible
  (non-integer, non-numeric or outside 0-23) means the row is internally corrupt, so it
  is rejected. A valid hour that disagrees with the timestamp is a warning, and the
  timestamp's hour is used.
- variable_cost_inr is optional. Blank means "not recorded" (a completeness gap). A
  value that is not a number, or is negative, is reported and discarded (treated as not
  recorded), never guessed or corrected.
"""

from __future__ import annotations

import pandas as pd

from app.schemas.analytics import DataIssue, Severity, ValidationReport

REQUIRED_COLUMNS = ("order_id", "merchant_id", "order_ts", "order_value_inr")
COMPLETENESS_COLUMNS = ("customer_id", "variable_cost_inr")

_MESSAGES = {
    "missing_columns": "Required columns are missing; no rows can be used.",
    "missing_order_id": "Rows without an order id were rejected.",
    "missing_merchant_id": "Rows without a merchant id were rejected.",
    "unknown_merchant": "Rows for merchants not in the merchant list were rejected.",
    "unparseable_timestamp": "Rows with an invalid order timestamp were rejected.",
    "invalid_hour": "Rows whose hour is not a whole number were rejected.",
    "hour_out_of_range": "Rows with an hour outside 0-23 were rejected.",
    "missing_order_value": "Rows without a usable order value were rejected.",
    "negative_order_value": "Rows with a negative order value were rejected.",
    "zero_order_value": "Rows with a zero order value were rejected.",
    "duplicate_order_id": "Order ids that appear more than once were all rejected.",
    "hour_mismatch": "Hour differs from the order timestamp; the timestamp's hour is used.",
    "unparseable_variable_cost": "Variable cost is not a number; treated as not recorded.",
    "negative_variable_cost": "Variable cost is negative; treated as not recorded.",
    "cost_exceeds_value": "Variable cost is higher than the order value; kept, but check costs.",
    "missing_variable_cost": "Some orders have no variable cost; profit may be estimated.",
}

ERROR_ORDER = (
    "missing_order_id",
    "missing_merchant_id",
    "unknown_merchant",
    "unparseable_timestamp",
    "invalid_hour",
    "hour_out_of_range",
    "missing_order_value",
    "negative_order_value",
    "zero_order_value",
    "duplicate_order_id",
)
WARNING_ORDER = (
    "hour_mismatch",
    "unparseable_variable_cost",
    "negative_variable_cost",
    "cost_exceeds_value",
    "missing_variable_cost",
)


def _blank(series: pd.Series) -> pd.Series:
    """True where the value is missing or an empty / whitespace-only string."""
    text = series.astype("string").str.strip()
    return series.isna() | (text == "").fillna(False)


def _examples(df: pd.DataFrame, mask: pd.Series) -> list[str]:
    out = []
    for idx in df.index[mask][:5]:
        oid = df.at[idx, "order_id"]
        out.append(f"row {idx}" if pd.isna(oid) or str(oid).strip() == "" else str(oid))
    return out


def _issue(df: pd.DataFrame, code: str, severity: Severity, mask: pd.Series) -> DataIssue:
    return DataIssue(
        code=code,
        severity=severity,
        count=int(mask.sum()),
        message=_MESSAGES[code],
        examples=_examples(df, mask),
    )


def validate_orders(
    df: pd.DataFrame, known_merchants: set[str] | None = None
) -> tuple[pd.DataFrame, ValidationReport]:
    """Return clean rows (with parsed `ts`, `date`, `hour`, `weekday` columns) and a report."""
    received = len(df)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        issue = DataIssue(
            code="missing_columns",
            severity="error",
            count=received,
            message=_MESSAGES["missing_columns"],
            examples=missing,
        )
        report = ValidationReport(
            rows_received=received,
            rows_valid=0,
            rows_rejected=received,
            issues=[issue],
            field_completeness={},
        )
        return df.iloc[0:0].copy(), report

    work = df.reset_index(drop=True).copy()
    merchant = work["merchant_id"].astype("string").str.strip()
    ts = pd.to_datetime(work["order_ts"], errors="coerce", format="ISO8601")
    value = pd.to_numeric(work["order_value_inr"], errors="coerce")
    no_id = _blank(work["order_id"])

    # Supplied hour (optional): must be a whole number 0-23 if present.
    if "hour" in work.columns:
        hour_given = ~_blank(work["hour"])
        hour_num = pd.to_numeric(work["hour"], errors="coerce")
        invalid_hour = hour_given & (hour_num.isna() | (hour_num % 1 != 0))
        out_of_range = hour_given & ~invalid_hour & ~hour_num.between(0, 23)
        hour_mismatch = (
            hour_given & ~invalid_hour & ~out_of_range & ts.notna() & (hour_num != ts.dt.hour)
        )
    else:
        invalid_hour = out_of_range = hour_mismatch = pd.Series(False, index=work.index)

    errors: dict[str, pd.Series] = {
        "missing_order_id": no_id,
        "missing_merchant_id": merchant.isna() | (merchant == ""),
        "unknown_merchant": pd.Series(False, index=work.index),
        "unparseable_timestamp": ts.isna(),
        "invalid_hour": invalid_hour,
        "hour_out_of_range": out_of_range,
        "missing_order_value": value.isna(),
        "negative_order_value": value < 0,
        "zero_order_value": value == 0,
        # Missing ids are reported once, as missing, not as duplicates of each other.
        "duplicate_order_id": ~no_id & work["order_id"].duplicated(keep=False),
    }
    if known_merchants is not None:
        errors["unknown_merchant"] = merchant.notna() & ~merchant.isin(known_merchants)

    issues: list[DataIssue] = []
    rejected = pd.Series(False, index=work.index)
    for code in ERROR_ORDER:
        mask = errors[code].fillna(False).astype(bool)
        rejected |= mask
        if mask.any():
            issues.append(_issue(work, code, "error", mask))

    # Variable cost (optional): blank = not recorded; non-numeric or negative = discarded.
    if "variable_cost_inr" in work.columns:
        cost_blank = _blank(work["variable_cost_inr"])
        cost = pd.to_numeric(work["variable_cost_inr"], errors="coerce")
        unparseable_cost = ~cost_blank & cost.isna()
        negative_cost = cost.notna() & (cost < 0)
        cost = cost.mask(negative_cost)
    else:
        cost_blank = pd.Series(True, index=work.index)
        cost = pd.Series(float("nan"), index=work.index)
        unparseable_cost = negative_cost = pd.Series(False, index=work.index)

    kept = ~rejected
    warnings = {
        "hour_mismatch": kept & hour_mismatch,
        "unparseable_variable_cost": kept & unparseable_cost,
        "negative_variable_cost": kept & negative_cost,
        "cost_exceeds_value": kept & cost.notna() & value.notna() & (cost > value),
        "missing_variable_cost": kept & cost_blank,
    }
    for code in WARNING_ORDER:
        mask = warnings[code].fillna(False).astype(bool)
        if mask.any():
            issues.append(_issue(work, code, "warning", mask))

    clean = work.loc[kept].copy()
    clean["merchant_id"] = merchant[kept]
    clean["ts"] = ts[kept]
    clean["order_value_inr"] = value[kept]
    clean["variable_cost_inr"] = cost[kept]
    clean["date"] = clean["ts"].dt.date
    clean["hour"] = clean["ts"].dt.hour  # timestamp is authoritative
    clean["weekday"] = clean["ts"].dt.day_of_week
    if "customer_id" not in clean.columns:
        clean["customer_id"] = pd.NA

    completeness = {}
    for col in COMPLETENESS_COLUMNS:
        if len(clean) and col in clean.columns:
            completeness[col] = round(float(clean[col].notna().mean()), 4)
        else:
            completeness[col] = 0.0

    report = ValidationReport(
        rows_received=received,
        rows_valid=int(kept.sum()),
        rows_rejected=int(rejected.sum()),
        issues=issues,
        field_completeness=completeness,
    )
    return clean.reset_index(drop=True), report
