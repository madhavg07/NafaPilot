"""Fixed specifications for the synthetic dataset.

Everything here is invented. Merchant names, cities and numbers are illustrative and do
not describe real businesses. Specs are hand-written (not random) so each merchant has a
known role in testing; the generator adds seeded noise on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

GENERATOR_VERSION = "2.0.0"
DEFAULT_SEED = 42

# Order history window: 12 full weeks, Monday 2026-08-03 to Sunday 2026-10-25.
N_WEEKS = 12
WINDOW_START = date(2026, 8, 3)
WINDOW_END = WINDOW_START + timedelta(days=7 * N_WEEKS - 1)

# Historical campaigns ran before the order window, so orders.csv contains no campaign
# orders. Campaign results live only in assignments.csv and outcomes.csv.
CAMPAIGN_PERIOD_START = date(2026, 5, 4)

# Anand Café (M001): mean orders per weekday in the 14:00-17:00 slot, week by week.
# Weeks 1-4 are the comparison period; weeks 5-12 reproduce the deck chart (38 -> 30).
ANAND_AFTERNOON_WEEKLY: tuple[int, ...] = (38, 38, 38, 38, 38, 38, 37, 36, 34, 33, 31, 30)
AFTERNOON_HOURS: tuple[int, ...] = (14, 15, 16)


def _profile(weights: dict[int, float]) -> tuple[float, ...]:
    total = sum(weights.values())
    return tuple(weights.get(h, 0.0) / total for h in range(24))


HOUR_PROFILES: dict[str, tuple[float, ...]] = {
    "cafe": _profile(
        {
            8: 3,
            9: 6,
            10: 6,
            11: 5,
            12: 7,
            13: 7,
            14: 12,
            15: 12,
            16: 11,
            17: 9,
            18: 8,
            19: 6,
            20: 5,
            21: 3,
        }
    ),
    "kirana": _profile(
        {
            7: 6,
            8: 8,
            9: 8,
            10: 6,
            11: 5,
            12: 4,
            13: 4,
            14: 3,
            15: 3,
            16: 4,
            17: 6,
            18: 9,
            19: 10,
            20: 9,
            21: 6,
            22: 3,
        }
    ),
    "restaurant": _profile(
        {11: 3, 12: 9, 13: 10, 14: 6, 15: 2, 16: 2, 17: 3, 18: 5, 19: 10, 20: 12, 21: 10, 22: 6}
    ),
    "sweets": _profile(
        {9: 3, 10: 4, 11: 5, 12: 5, 13: 4, 14: 4, 15: 5, 16: 7, 17: 9, 18: 10, 19: 10, 20: 8, 21: 5}
    ),
    "salon": _profile({h: 1.0 for h in range(10, 21)}),
    "pharmacy": _profile({h: (1.4 if h in (9, 10, 19, 20) else 1.0) for h in range(8, 23)}),
}


@dataclass(frozen=True)
class MerchantSpec:
    merchant_id: str
    name: str
    category: str
    city: str
    aov: int  # average order value, ₹
    vc_per_order: float | None  # variable cost per average order, ₹; None = unknown
    daily_orders: float  # mean orders on a weekday
    scenario_tag: str
    trend_per_week: float = 0.0  # multiplicative, e.g. 0.02 = +2% a week
    weekend_factor: float = 1.2
    history_weeks: int = N_WEEKS  # fewer = short history
    gap: tuple[int, int] | None = None  # (start day offset, length) with no records
    missing_cost_share: float = 0.0  # share of orders with no recorded variable cost
    walk_in_share: float = 0.25  # share of orders with no customer id
    customer_pool: int = 600
    # Policy (version 1)
    discount_cap_pct: float = 15.0
    min_margin_pct: float = 25.0
    max_loss_inr: int = 1000
    budget_inr: int = 5000
    holdout_pct: int = 20


# The four shops shown in the app. Each has a 15-18 product catalogue (see CATALOGUES).
MERCHANTS: tuple[MerchantSpec, ...] = (
    MerchantSpec(
        "M001",
        "Anand Café",
        "cafe",
        "Noida",
        200,
        110,
        110,
        "anand_afternoon_decline",
        weekend_factor=1.3,
        customer_pool=1200,
    ),
    MerchantSpec(
        "M002", "Sharma General Store", "kirana", "Delhi", 120, 96, 60, "stable", customer_pool=900
    ),
    MerchantSpec(
        "M003",
        "Gupta Sweets",
        "sweets",
        "Ghaziabad",
        260,
        150,
        45,
        "growing",
        trend_per_week=0.02,
        weekend_factor=1.6,
    ),
    MerchantSpec(
        "M004",
        "Spice Route Kitchen",
        "restaurant",
        "Gurugram",
        450,
        270,
        40,
        "declining_overall",
        trend_per_week=-0.02,
    ),
)

# Edge cases used only by the automated tests (generate(include_test_merchants=True)).
# They are not part of the shipped dataset, so the app shows just the four shops above.
TEST_MERCHANTS: tuple[MerchantSpec, ...] = (
    MerchantSpec(
        "M007", "Chai Point Corner", "cafe", "Delhi", 90, None, 70, "missing_variable_cost"
    ),
    MerchantSpec(
        "M011",
        "Kumar Kirana",
        "kirana",
        "Ghaziabad",
        140,
        112,
        45,
        "partial_cost_and_customer_ids",
        missing_cost_share=0.3,
        walk_in_share=0.6,
    ),
    MerchantSpec(
        "M015",
        "New Leaf Café",
        "cafe",
        "Noida",
        210,
        115,
        30,
        "short_history",
        history_weeks=3,
        customer_pool=250,
    ),
    MerchantSpec(
        "M019", "Momo Junction", "restaurant", "Ghaziabad", 160, 85, 50, "data_gap", gap=(40, 10)
    ),
)

ALL_MERCHANTS: tuple[MerchantSpec, ...] = MERCHANTS + TEST_MERCHANTS


# ------------------------------------------------------------------ product catalogues


@dataclass(frozen=True)
class ProductSpec:
    sku: str
    name: str
    category: str
    price_inr: int
    unit_cost_inr: int  # before the per-merchant calibration in the generator
    popularity: float  # relative chance of being the first item in a basket
    companions: tuple[tuple[str, float], ...] = ()  # (sku, chance it is added too)


def _p(
    sku: str,
    name: str,
    category: str,
    price: int,
    cost: int,
    popularity: float,
    *companions: tuple[str, float],
) -> ProductSpec:
    return ProductSpec(sku, name, category, price, cost, popularity, tuple(companions))


# Co-purchase links are planted on purpose (e.g. cappuccino with a sandwich) so the
# basket analysis has real patterns to find. Prices and costs are illustrative.
CATALOGUES: dict[str, tuple[ProductSpec, ...]] = {
    "cafe": (
        _p("CF001", "Cappuccino", "coffee", 180, 80, 10, ("SW001", 0.45), ("PS001", 0.2)),
        _p("CF002", "Cold coffee", "coffee", 160, 70, 7, ("BR001", 0.25)),
        _p("CF003", "Masala chai", "tea", 40, 12, 9, ("MG001", 0.25), ("CK001", 0.1)),
        _p("CF004", "Espresso", "coffee", 120, 45, 4, ("CK001", 0.3)),
        _p("CF005", "Green tea", "tea", 90, 30, 3),
        _p("SW001", "Veg sandwich", "snacks", 150, 100, 6, ("CD001", 0.15)),
        _p("SW002", "Paneer tikka sandwich", "snacks", 180, 115, 4, ("CF002", 0.2)),
        _p("BK001", "Veg burger", "meals", 220, 130, 6, ("CD001", 0.35), ("FR001", 0.3)),
        _p("BK002", "Aloo tikki burger", "meals", 140, 85, 4, ("CD001", 0.3)),
        _p("PS001", "Chocolate pastry", "desserts", 100, 70, 5),
        _p("PS002", "Blueberry muffin", "desserts", 110, 65, 3, ("CF001", 0.3)),
        _p("CK001", "Butter croissant", "bakery", 120, 75, 3),
        _p("CH001", "Potato chips", "snacks", 40, 18, 3, ("CD001", 0.4)),
        _p("CD001", "Cold drink", "beverages", 60, 25, 4, ("CH001", 0.3)),
        _p("FR001", "French fries", "snacks", 110, 50, 4, ("CD001", 0.3)),
        _p("MG001", "Masala Maggi", "meals", 90, 40, 4, ("CF003", 0.3)),
        _p("WP001", "Veg wrap", "meals", 170, 105, 3, ("CD001", 0.25)),
        _p("BR001", "Brownie", "desserts", 90, 50, 3),
    ),
    "kirana": (
        _p("AT001", "Atta 5 kg", "staples", 260, 215, 4, ("DL001", 0.3)),
        _p("RC001", "Rice 1 kg", "staples", 80, 64, 5, ("DL001", 0.3)),
        _p("DL001", "Toor dal 1 kg", "staples", 160, 130, 3),
        _p("SG001", "Sugar 1 kg", "staples", 48, 40, 5),
        _p("ML001", "Milk 1 L", "dairy", 66, 58, 12, ("BD001", 0.25)),
        _p("BD001", "Bread", "bakery", 45, 34, 7, ("BT001", 0.4), ("EG001", 0.3)),
        _p("EG001", "Eggs, 6 pieces", "dairy", 48, 38, 5),
        _p("BT001", "Butter 100 g", "dairy", 58, 48, 3),
        _p("BS001", "Biscuits", "snacks", 30, 22, 7, ("ML001", 0.2)),
        _p("NM001", "Namkeen 200 g", "snacks", 40, 28, 5, ("CD002", 0.2)),
        _p("TE001", "Tea 250 g", "staples", 140, 110, 3, ("SG001", 0.3), ("BS001", 0.35)),
        _p("SP001", "Bath soap", "personal care", 45, 32, 4),
        _p("TP001", "Toothpaste", "personal care", 95, 72, 3),
        _p("NO001", "Instant noodles pack", "snacks", 56, 44, 5),
        _p("CD002", "Cold drink 2 L", "beverages", 99, 70, 3, ("CH002", 0.35)),
        _p("CH002", "Chips", "snacks", 20, 14, 5, ("CD002", 0.15)),
        _p("OL001", "Cooking oil 1 L", "staples", 155, 132, 3),
        _p("DT001", "Detergent 1 kg", "household", 120, 95, 2),
    ),
    "sweets": (
        _p("KK001", "Kaju katli 250 g", "mithai", 300, 170, 6),
        _p("RG001", "Rasgulla 1 kg tin", "mithai", 220, 120, 4, ("GJ001", 0.15)),
        _p("GJ001", "Gulab jamun 500 g", "mithai", 180, 95, 5),
        _p("LD001", "Motichoor laddoo 500 g", "mithai", 240, 135, 5),
        _p("SP002", "Soan papdi box", "mithai", 200, 110, 3),
        _p("BF001", "Milk barfi 250 g", "mithai", 160, 90, 4),
        _p("SM001", "Samosa, 2 pieces", "snacks", 40, 18, 8, ("JL001", 0.3), ("LS001", 0.2)),
        _p("KC001", "Kachori", "snacks", 30, 14, 5, ("SM001", 0.3)),
        _p("DK001", "Dhokla 250 g", "snacks", 80, 40, 3, ("NK001", 0.2)),
        _p("JL001", "Jalebi 250 g", "mithai", 90, 45, 4),
        _p("RM001", "Rasmalai, 2 pieces", "mithai", 120, 65, 3),
        _p("NK001", "Namkeen mix 200 g", "snacks", 90, 50, 3),
        _p("LS001", "Sweet lassi", "beverages", 60, 25, 3),
        _p("PP001", "Paneer pakoda", "snacks", 120, 65, 3, ("LS001", 0.25)),
        _p("GB001", "Festive gift box", "gifting", 450, 280, 3),
        _p("CB001", "Chole bhature", "meals", 120, 60, 4, ("LS001", 0.4)),
    ),
    "restaurant": (
        _p("PB001", "Paneer butter masala", "mains", 280, 160, 6, ("NA001", 0.7)),
        _p("DM001", "Dal makhani", "mains", 220, 110, 5, ("NA001", 0.6), ("JR001", 0.35)),
        _p("NA001", "Butter naan", "breads", 50, 18, 3),
        _p("TR001", "Tandoori roti", "breads", 25, 8, 2),
        _p("VB001", "Veg biryani", "rice", 240, 130, 5, ("RT001", 0.5)),
        _p("CB002", "Chicken biryani", "rice", 320, 190, 6, ("RT001", 0.5)),
        _p("BC001", "Butter chicken", "mains", 360, 220, 6, ("NA001", 0.7)),
        _p("JR001", "Jeera rice", "rice", 160, 70, 2),
        _p("RT001", "Raita", "sides", 70, 30, 2),
        _p("GJ002", "Gulab jamun, 2 pieces", "desserts", 80, 35, 2),
        _p("LS002", "Lassi", "beverages", 90, 35, 3),
        _p("SD001", "Soft drink", "beverages", 60, 25, 3),
        _p("VM001", "Veg manchurian", "chinese", 220, 115, 4, ("HN001", 0.45)),
        _p("HN001", "Hakka noodles", "chinese", 200, 100, 3),
        _p("TC001", "Tandoori chicken, half", "starters", 340, 200, 4, ("SD001", 0.3)),
        _p("MP001", "Masala papad", "starters", 60, 20, 2),
        _p("MV001", "Mixed veg", "mains", 210, 110, 3, ("TR001", 0.5)),
    ),
}


@dataclass(frozen=True)
class CampaignSpec:
    """A historical campaign with a known true effect (the ground truth).

    Per eligible customer over the campaign window:
      base orders  ~ Poisson(base_rate)          (would have happened anyway)
      extra orders ~ Poisson(lift) if treated    (caused by the offer)
    Every extra order redeems the discount; each treated base order redeems with
    probability redeem_share_base. Holdout customers never see the offer.
    """

    campaign_id: str
    merchant_id: str
    campaign_type: str
    discount_inr: int
    target_group: str
    campaign_cost_inr: int
    eligible: int
    base_rate: float
    lift: float
    redeem_share_base: float
    start_offset_days: int
    duration_days: int = 28
    note: str = ""


# Planted campaigns, each with a deliberate role. Others are drawn at random per merchant.
PLANTED_CAMPAIGNS: tuple[CampaignSpec, ...] = (
    # Mirrors the deck's slide 6 experiment: 300 eligible, 240/60 split, +0.20 per customer.
    CampaignSpec(
        "C001",
        "M001",
        "targeted_offer",
        20,
        "eligible_existing_customers",
        1000,
        300,
        0.45,
        0.20,
        50 / 108,
        0,
        note="deck slide 6 analogue; profitable",
    ),
    # 25% off against a 15% cap: a policy breach that happened before limits were set.
    CampaignSpec(
        "C002",
        "M002",
        "off_peak",
        30,
        "all_walk_ins",
        800,
        400,
        0.9,
        0.15,
        0.7,
        7,
        note="discount above cap",
    ),
    # Discounts many orders that would have happened anyway; small lift: loses money.
    CampaignSpec(
        "C003",
        "M003",
        "targeted_offer",
        20,
        "eligible_existing_customers",
        500,
        300,
        0.3,
        0.05,
        0.5,
        14,
        note="unprofitable within loss limit",
    ),
    # Large off-peak discount with weak response: loss far beyond the ₹1,000 limit.
    CampaignSpec(
        "C004",
        "M004",
        "off_peak",
        65,
        "all_walk_ins",
        2500,
        500,
        0.6,
        0.04,
        0.9,
        21,
        note="loss limit violation",
    ),
    # The offer does nothing: true lift is zero, so any measured uplift is noise.
    CampaignSpec(
        "C005",
        "M002",
        "targeted_offer",
        15,
        "eligible_existing_customers",
        600,
        300,
        0.5,
        0.0,
        0.4,
        28,
        note="null effect",
    ),
)

# Merchants that get one extra random campaign (when present). M007 (no cost data) and
# M015 (too new) have no campaign history by design.
RANDOM_CAMPAIGN_MERCHANTS: tuple[str, ...] = ("M001", "M004", "M011", "M019")
