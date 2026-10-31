"""Synthetic dataset: determinism, integrity, planted scenarios and ground truth."""

from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.synthetic import Dataset, generate, write_dataset
from app.synthetic.specs import (
    ALL_MERCHANTS,
    ANAND_AFTERNOON_WEEKLY,
    CATALOGUES,
    MERCHANTS,
    WINDOW_END,
    WINDOW_START,
)

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


@pytest.fixture(scope="module")
def ds() -> Dataset:
    return generate(42, include_test_merchants=True)


# ------------------------------------------------------------------ reproducibility


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    a = write_dataset(generate(42, include_test_merchants=True), tmp_path / "a")
    b = write_dataset(generate(42, include_test_merchants=True), tmp_path / "b")
    assert a == b
    for name in a["files"]:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_shipped_dataset_has_four_shops() -> None:
    shipped = generate(42)
    assert sorted(shipped["merchants"]["merchant_id"]) == ["M001", "M002", "M003", "M004"]
    assert [m.merchant_id for m in MERCHANTS] == ["M001", "M002", "M003", "M004"]
    counts = shipped["products"].groupby("merchant_id").size()
    assert counts.between(15, 20).all()


def test_display_shops_do_not_depend_on_test_merchants(ds: Dataset) -> None:
    shipped = generate(42)
    for name in ("orders", "order_items", "products"):
        a = shipped[name]
        b = ds[name][ds[name]["merchant_id"].isin(a["merchant_id"].unique())]
        assert a.reset_index(drop=True).equals(b.reset_index(drop=True)), name


def test_different_seed_changes_data(ds: Dataset) -> None:
    other = generate(7, include_test_merchants=True)
    assert not ds["orders"]["order_value_inr"].equals(other["orders"]["order_value_inr"])


def test_window_is_twelve_full_weeks() -> None:
    assert WINDOW_START.weekday() == 0 and WINDOW_END.weekday() == 6
    assert (WINDOW_END - WINDOW_START).days == 83


# ----------------------------------------------------------------------- integrity


def test_foreign_keys_resolve(ds: Dataset) -> None:
    merchants = set(ds["merchants"]["merchant_id"])
    customers = set(ds["customers"]["customer_id"])
    campaigns = set(ds["campaigns"]["campaign_id"])
    assert set(ds["merchant_policies"]["merchant_id"]) == merchants
    assert set(ds["orders"]["merchant_id"]) <= merchants
    assert set(ds["orders"]["customer_id"].dropna()) <= customers
    assert set(ds["campaigns"]["merchant_id"]) <= merchants
    assert set(ds["assignments"]["campaign_id"]) == campaigns
    assert set(ds["assignments"]["customer_id"]) <= customers
    fb = ds["feedback"]
    assert set(fb["merchant_id"]) <= merchants
    assert set(fb["campaign_id"].dropna()) <= campaigns


def test_order_items_add_up_to_orders(ds: Dataset) -> None:
    o = ds["orders"].set_index("order_id")
    it = ds["order_items"]
    assert set(it["order_id"]) == set(o.index)  # every order has at least one line
    value = it.groupby("order_id")["line_value_inr"].sum()
    assert (value == o.loc[value.index, "order_value_inr"]).all()
    known = o["variable_cost_inr"].notna()
    cost = it.groupby("order_id")["line_cost_inr"].sum(min_count=1)
    diff = (cost[known[known].index] - o.loc[known, "variable_cost_inr"]).abs()
    assert (diff < 0.01).all()
    assert (it["quantity"] >= 1).all()
    assert not it.duplicated(["order_id", "sku"]).any()


def test_products_belong_to_their_merchant(ds: Dataset) -> None:
    p = ds["products"]
    assert not p.duplicated(["merchant_id", "sku"]).any()
    keys = set(zip(p["merchant_id"], p["sku"], strict=True))
    it = ds["order_items"]
    assert set(zip(it["merchant_id"], it["sku"], strict=True)) <= keys
    priced = p.set_index(["merchant_id", "sku"])["price_inr"]
    lines = it.set_index(["merchant_id", "sku"])["unit_price_inr"]
    assert (lines == priced.loc[lines.index].to_numpy()).all()
    known = p.dropna(subset=["unit_cost_inr"])
    assert (known["unit_cost_inr"] < known["price_inr"]).all()
    for m in MERCHANTS:
        assert len(p[p["merchant_id"] == m.merchant_id]) == len(CATALOGUES[m.category])


def test_planted_co_purchase_is_visible(ds: Dataset) -> None:
    """Cappuccino is bought with a veg sandwich far more often than chance (planted)."""
    it = ds["order_items"]
    m1 = it[it["merchant_id"] == "M001"]
    has = m1.pivot_table(index="order_id", columns="sku", values="quantity", aggfunc="sum")
    has = has.notna()
    p_sw = has["SW001"].mean()
    p_sw_given_cf = has.loc[has["CF001"], "SW001"].mean()
    assert p_sw_given_cf > 1.5 * p_sw


def test_customers_belong_to_order_merchant(ds: Dataset) -> None:
    o = ds["orders"].dropna(subset=["customer_id"])
    assert (o["customer_id"].str.slice(0, 4) == o["merchant_id"]).all()


def test_ids_unique(ds: Dataset) -> None:
    assert ds["orders"]["order_id"].is_unique
    assert ds["customers"]["customer_id"].is_unique
    assert ds["campaigns"]["campaign_id"].is_unique
    assert not ds["assignments"].duplicated(["campaign_id", "customer_id"]).any()


def test_orders_are_valid(ds: Dataset) -> None:
    o = ds["orders"]
    assert (o["order_value_inr"] > 0).all()
    assert o["hour"].between(0, 23).all()
    ts = pd.to_datetime(o["order_ts"])
    assert ts.min().date() >= WINDOW_START and ts.max().date() <= WINDOW_END
    assert (o["discount_inr"] == 0).all()  # campaign orders are not in orders.csv


def test_everything_labelled_synthetic(ds: Dataset) -> None:
    for name in ("merchants", "customers", "orders", "campaigns", "feedback"):
        assert ds[name]["is_synthetic"].all(), name
    assert ds["outcomes"]["is_simulated"].all()


def test_no_personal_data_columns(ds: Dataset) -> None:
    banned = {"phone", "email", "address", "mobile", "upi", "aadhaar", "pan"}
    for name, df in ds.tables.items():
        cols = {c.lower() for c in df.columns}
        assert not cols & banned, name


# --------------------------------------------------------------- planted scenarios


def test_anand_afternoon_decline_matches_deck(ds: Dataset) -> None:
    o = ds["orders"]
    slot = o[
        (o["merchant_id"] == "M001") & o["hour"].isin([14, 15, 16]) & o["weekday"].isin(WEEKDAYS)
    ]
    week = (pd.to_datetime(slot["order_date"]) - pd.Timestamp(WINDOW_START)).dt.days // 7
    per_day = (slot.groupby(week).size() / 5).tolist()
    assert per_day == list(ANAND_AFTERNOON_WEEKLY)
    assert per_day[-8:] == [38, 38, 37, 36, 34, 33, 31, 30]  # deck slide 2


def test_anand_economics_match_deck(ds: Dataset) -> None:
    m1 = ds["orders"][ds["orders"]["merchant_id"] == "M001"]
    assert m1["order_value_inr"].mean() == pytest.approx(200, abs=5)
    assert m1["variable_cost_inr"].mean() == pytest.approx(110, abs=3)
    pol = ds["merchant_policies"].set_index("merchant_id").loc["M001"]
    assert pol["discount_cap_pct"] == 15 and pol["max_loss_inr"] == 1000
    assert pol["holdout_pct"] == 20


def test_missing_cost_merchant(ds: Dataset) -> None:
    m = ds["merchants"].set_index("merchant_id")
    assert pd.isna(m.loc["M007", "variable_cost_per_order_inr"])
    o = ds["orders"]
    assert o.loc[o["merchant_id"] == "M007", "variable_cost_inr"].isna().all()
    assert "M007" not in set(ds["campaigns"]["merchant_id"])


def test_partial_cost_and_id_gaps(ds: Dataset) -> None:
    o = ds["orders"][ds["orders"]["merchant_id"] == "M011"]
    assert 0.2 < o["variable_cost_inr"].isna().mean() < 0.4
    assert 0.5 < o["customer_id"].isna().mean() < 0.7


def test_short_history(ds: Dataset) -> None:
    o = ds["orders"][ds["orders"]["merchant_id"] == "M015"]
    first = pd.to_datetime(o["order_date"]).min().date()
    assert (WINDOW_END - first).days < 21


def test_data_gap(ds: Dataset) -> None:
    o = ds["orders"]
    dates = set(o.loc[o["merchant_id"] == "M019", "order_date"])
    spec = next(m for m in ALL_MERCHANTS if m.merchant_id == "M019")
    assert spec.gap is not None
    start, length = spec.gap
    missing = {(WINDOW_START + timedelta(days=start + i)).isoformat() for i in range(length)}
    assert not dates & missing
    assert len(dates) == 84 - length


def test_invalid_orders_cover_each_defect(ds: Dataset) -> None:
    issues = set(ds["invalid_orders"]["expected_issue"])
    assert issues == {
        "negative_order_value",
        "hour_out_of_range",
        "missing_merchant_id",
        "unparseable_timestamp",
        "unknown_merchant",
        "cost_exceeds_value",
        "missing_order_value",
        "duplicate_order_id",
    }
    # And none of them leaked into the clean orders table.
    assert not ds["orders"]["order_id"].str.startswith("BAD").any()


# ------------------------------------------------------------------- campaigns


def test_every_outcome_class_is_planted(ds: Dataset) -> None:
    labels = set(ds["campaigns"]["outcome_label"])
    assert {
        "profitable",
        "unprofitable",
        "loss_limit_violation",
        "discount_cap_breach",
        "null_effect",
    } <= labels


def test_deck_campaign_ground_truth(ds: Dataset) -> None:
    c = ds["campaigns"].set_index("campaign_id").loc["C001"]
    assert c["merchant_id"] == "M001" and c["discount_inr"] == 20
    # 48 incremental × ₹70 − 50 subsidised × ₹20 − ₹1,000 = ₹1,360 (deck slide 6).
    assert c["true_expected_incremental_profit_inr"] == pytest.approx(1360)


def test_holdout_split_is_exact(ds: Dataset) -> None:
    a = ds["assignments"]
    c001 = a[a["campaign_id"] == "C001"]["group"].value_counts()
    assert c001["treatment"] == 240 and c001["holdout"] == 60
    camp = ds["campaigns"].set_index("campaign_id")
    for cid, grp in a.groupby("campaign_id"):
        expected = round(camp.loc[cid, "eligible_customers"] * camp.loc[cid, "holdout_pct"] / 100)
        assert (grp["group"] == "holdout").sum() == expected


def test_holdout_never_redeems(ds: Dataset) -> None:
    out = ds["outcomes"]
    assert (out.loc[out["group"] == "holdout", "redemptions"] == 0).all()
    assert (out["redemptions"] <= out["orders"]).all()


def test_true_effects_are_recoverable(ds: Dataset) -> None:
    """Pooled across campaigns, measured uplift tracks the true lift (no bias)."""
    out = ds["outcomes"]
    rates = out.groupby(["campaign_id", "group"])["orders"].mean().unstack()
    measured = rates["treatment"] - rates["holdout"]
    truth = ds["campaigns"].set_index("campaign_id")["true_lift_per_customer"]
    assert (measured - truth).mean() == pytest.approx(0, abs=0.05)


# --------------------------------------------------------------------- feedback


def test_planted_feedback(ds: Dataset) -> None:
    fb = ds["feedback"]
    anand = fb[(fb["merchant_id"] == "M001") & (fb["decision"] == "rejected")].iloc[0]
    assert anand["preference_key"] == "target"
    assert anand["preference_value"] == "new_customers"
    assert "existing customers" in anand["reason"]
    no_reason = fb[(fb["decision"] == "rejected") & fb["reason"].isna()]
    assert len(no_reason) >= 1
    assert "edited" in set(fb["decision"])
    # A valid reason that should NOT become a stored preference.
    strategic = fb[(fb["merchant_id"] == "M002") & (fb["decision"] == "rejected")].iloc[0]
    assert pd.isna(strategic["preference_key"])
