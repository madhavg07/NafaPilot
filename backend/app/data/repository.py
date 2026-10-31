"""Read access to merchant data. The only module that touches dataset files.

Milestone 6 replaces the CSV source with PostgreSQL behind the same methods.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from app.schemas.decision import PolicyConfig
from app.schemas.settings import DEFAULT_CAMPAIGN_COSTS, SavedSettings
from app.services.analytics.baseline import MerchantInfo

_ID_COLUMNS = {
    "merchant_id": "string",
    "customer_id": "string",
    "order_id": "string",
    "sku": "string",
}


def _optional_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=_ID_COLUMNS) if path.exists() else pd.DataFrame()


class DatasetMissing(Exception):
    """The synthetic dataset has not been generated yet."""


class UnknownMerchant(Exception):
    pass


def _dec(value: Any) -> Decimal | None:
    return None if pd.isna(value) else Decimal(str(value))


@dataclass
class DataRepository:
    merchants_df: pd.DataFrame
    policies_df: pd.DataFrame
    orders_df: pd.DataFrame
    data_source: str
    is_synthetic: bool = True
    # Optional (M8): customers and past experiment outcomes. Empty when not available.
    customers_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    history_outcomes_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Merchant-edited settings (latest saved version), looked up per merchant. None = use the
    # dataset's policy only. Set per request by app.api.deps; never shared between requests.
    settings_lookup: Callable[[str], SavedSettings | None] | None = field(
        default=None, repr=False, compare=False
    )
    # Product catalogue and order lines. Empty when the data has no product detail; the
    # merchant-level workflow still works then.
    products_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    items_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    # The merchant's saved product selection: (version, skus) or None = every product.
    selection_lookup: Callable[[str], tuple[int, list[str]] | None] | None = field(
        default=None, repr=False, compare=False
    )

    def with_settings(
        self,
        lookup: Callable[[str], SavedSettings | None],
        selection: Callable[[str], tuple[int, list[str]] | None] | None = None,
    ) -> DataRepository:
        """A copy that reads the merchant's saved settings (DataFrames are shared, not copied)."""
        return replace(self, settings_lookup=lookup, selection_lookup=selection)

    # --------------------------------------------------------------- products

    def products_for(self, merchant_id: str) -> pd.DataFrame:
        self._merchant_row(merchant_id)
        p = self.products_df
        if p.empty or "merchant_id" not in p:
            return pd.DataFrame(
                columns=["merchant_id", "sku", "name", "category", "price_inr", "unit_cost_inr"]
            )
        return p[p["merchant_id"] == merchant_id].sort_values("sku", ignore_index=True)

    def items_for(self, merchant_id: str) -> pd.DataFrame:
        self._merchant_row(merchant_id)
        it = self.items_df
        if it.empty or "merchant_id" not in it:
            return pd.DataFrame(
                columns=["order_id", "merchant_id", "sku", "quantity", "unit_price_inr"]
            )
        return it[it["merchant_id"] == merchant_id]

    def product_selection(self, merchant_id: str) -> tuple[str, int, list[str]]:
        """(source, version, skus) of the products the merchant allows in offers.
        Until the merchant saves a selection, every product is allowed (version 0)."""
        all_skus = list(self.products_for(merchant_id)["sku"])
        saved = None if self.selection_lookup is None else self.selection_lookup(merchant_id)
        if saved is None:
            return "default", 0, all_skus
        version, skus = saved
        return "merchant", version, [s for s in all_skus if s in set(skus)]

    def saved_settings(self, merchant_id: str) -> SavedSettings | None:
        return None if self.settings_lookup is None else self.settings_lookup(merchant_id)

    def default_policy(self, merchant_id: str) -> PolicyConfig | None:
        """The dataset's policy for this merchant, ignoring anything saved in the app."""
        self._merchant_row(merchant_id)
        pol = self.policies_df[self.policies_df["merchant_id"] == merchant_id]
        if pol.empty:
            return None
        latest = pol.sort_values("policy_version").iloc[-1]
        return PolicyConfig(
            policy_version=int(latest["policy_version"]),
            discount_cap_pct=Decimal(str(latest["discount_cap_pct"])),
            min_margin_pct=Decimal(str(latest["min_margin_pct"])),
            max_loss_inr=Decimal(str(latest["max_loss_inr"])),
            budget_inr=Decimal(str(latest["budget_inr"])),
            holdout_pct=Decimal(str(latest["holdout_pct"])),
            risk_scenario=str(latest["risk_scenario"]),
        )

    @classmethod
    def from_dir(cls, path: Path) -> DataRepository:
        needed = ["merchants.csv", "merchant_policies.csv", "orders.csv"]
        missing = [f for f in needed if not (path / f).exists()]
        if missing:
            raise DatasetMissing(
                f"Dataset files not found in {path} ({', '.join(missing)}). "
                "Run: python ../scripts/generate_dataset.py"
            )
        return cls(
            merchants_df=pd.read_csv(path / "merchants.csv", dtype=_ID_COLUMNS),
            policies_df=pd.read_csv(path / "merchant_policies.csv", dtype=_ID_COLUMNS),
            orders_df=pd.read_csv(path / "orders.csv", dtype=_ID_COLUMNS),
            data_source="synthetic_csv",
            customers_df=_optional_csv(path / "customers.csv"),
            history_outcomes_df=_optional_csv(path / "outcomes.csv"),
            products_df=_optional_csv(path / "products.csv"),
            items_df=_optional_csv(path / "order_items.csv"),
        )

    @classmethod
    def from_tables(cls, tables: dict[str, pd.DataFrame]) -> DataRepository:
        return cls(
            merchants_df=tables["merchants"],
            policies_df=tables["merchant_policies"],
            orders_df=tables["orders"],
            data_source="synthetic_in_memory",
            customers_df=tables.get("customers", pd.DataFrame()),
            history_outcomes_df=tables.get("outcomes", pd.DataFrame()),
            products_df=tables.get("products", pd.DataFrame()),
            items_df=tables.get("order_items", pd.DataFrame()),
        )

    # ----------------------------------------------------------------- queries

    @property
    def merchant_ids(self) -> set[str]:
        return set(self.merchants_df["merchant_id"])

    @property
    def as_of(self) -> date:
        """Last date in the dataset. Analyses are anchored here, not to today's date."""
        latest: date = pd.to_datetime(self.orders_df["order_date"]).max().date()
        return latest

    def list_merchants(self) -> list[dict[str, Any]]:
        cols = ["merchant_id", "name", "category", "city", "scenario_tag"]
        return [
            {k: (None if pd.isna(v) else v) for k, v in row.items()}
            for row in self.merchants_df[cols].to_dict("records")
        ]

    def _merchant_row(self, merchant_id: str) -> pd.Series:
        rows = self.merchants_df[self.merchants_df["merchant_id"] == merchant_id]
        if rows.empty:
            raise UnknownMerchant(merchant_id)
        return rows.iloc[0]

    def merchant_policy(self, merchant_id: str) -> PolicyConfig | None:
        """The policy in force: the merchant's latest saved settings, else the dataset's."""
        default = self.default_policy(merchant_id)  # raises UnknownMerchant
        saved = self.saved_settings(merchant_id)
        return saved.policy if saved is not None else default

    def merchant_profile(self, merchant_id: str) -> dict[str, Any]:
        m = self._merchant_row(merchant_id)
        effective = self.merchant_policy(merchant_id)
        policy = None if effective is None else _policy_dict(effective)
        vc = _dec(m["variable_cost_per_order_inr"])
        saved = self.saved_settings(merchant_id)
        return {
            "merchant_id": merchant_id,
            "name": m["name"],
            "category": m["category"],
            "city": m["city"],
            "scenario_tag": m["scenario_tag"],
            "average_order_value_inr": float(m["average_order_value_inr"]),
            "variable_cost_per_order_inr": None if vc is None else float(vc),
            "policy": policy,
            "settings_source": "merchant" if saved is not None else "default",
            "campaign_costs_inr": {
                k: (None if v is None else float(v))
                for k, v in (
                    saved.campaign_costs_inr if saved is not None else DEFAULT_CAMPAIGN_COSTS
                ).items()
            },
            "is_synthetic": self.is_synthetic,
            "data_source": self.data_source,
        }

    def merchant_info(self, merchant_id: str) -> MerchantInfo:
        m = self._merchant_row(merchant_id)
        return MerchantInfo(
            merchant_id=merchant_id,
            aov_setting_inr=_dec(m["average_order_value_inr"]),
            vc_setting_inr=_dec(m["variable_cost_per_order_inr"]),
            history_start=(
                None
                if "history_start" not in m.index or pd.isna(m["history_start"])
                else pd.to_datetime(m["history_start"]).date()
            ),
        )

    def customers_for(self, merchant_id: str) -> pd.DataFrame:
        """Synthetic customer records for one merchant (may be empty)."""
        self._merchant_row(merchant_id)
        if self.customers_df.empty or "merchant_id" not in self.customers_df:
            return pd.DataFrame(columns=["customer_id", "merchant_id", "orders_in_window"])
        return self.customers_df[self.customers_df["merchant_id"] == merchant_id]

    def historical_holdout(self, merchant_id: str) -> tuple[float | None, int]:
        """Mean orders per holdout customer in this merchant's past experiments.

        Holdout customers never saw an offer, so this is an observed (synthetic) base
        rate per customer over a campaign window. Returns (None, 0) if there is none.
        """
        self._merchant_row(merchant_id)
        h = self.history_outcomes_df
        if h.empty or not {"customer_id", "group", "orders"} <= set(h.columns):
            return None, 0
        mine = h[
            (h["group"] == "holdout")
            & h["customer_id"].astype(str).str.startswith(f"{merchant_id}-")
        ]
        if mine.empty:
            return None, 0
        return float(mine["orders"].mean()), int(len(mine))

    def orders_for(self, merchant_id: str) -> pd.DataFrame:
        self._merchant_row(merchant_id)
        return self.orders_df[self.orders_df["merchant_id"] == merchant_id]


def _policy_dict(p: PolicyConfig) -> dict[str, Any]:
    return {
        "policy_version": p.policy_version,
        "discount_cap_pct": float(p.discount_cap_pct),
        "min_margin_pct": float(p.min_margin_pct),
        "max_loss_inr": float(p.max_loss_inr),
        "budget_inr": float(p.budget_inr),
        "holdout_pct": float(p.holdout_pct),
        "risk_scenario": p.risk_scenario,
    }
