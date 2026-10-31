"""Read and save a merchant's own settings (policy limits, usual campaign costs, preferences).

Every save that changes limits or costs appends a new merchant_settings version whose
policy_version is one higher than the policy in force, so evaluations recorded earlier keep
showing the version they were checked against. Preferences are appended to
merchant_preferences (source 'merchant_settings'); clearing one appends an empty value.
Each save writes one 'settings_updated' audit event with the changed fields only.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.data import DataRepository
from app.db.models import AuditEvent, MerchantPreference, MerchantSetting
from app.schemas.decision import PolicyConfig
from app.schemas.settings import (
    COST_TYPES,
    DEFAULT_CAMPAIGN_COSTS,
    SavedSettings,
    SettingsIn,
    SettingsOut,
    policy_out,
)
from app.services.recommendations import current_preferences

PREF_KEYS = ("target", "max_discount_inr", "avoid_campaign_type")


def _to_saved(row: MerchantSetting) -> SavedSettings:
    costs = row.campaign_costs_inr or {}
    return SavedSettings(
        policy=PolicyConfig(
            policy_version=row.policy_version,
            discount_cap_pct=Decimal(row.discount_cap_pct),
            min_margin_pct=Decimal(row.min_margin_pct),
            max_loss_inr=Decimal(row.max_loss_inr),
            budget_inr=Decimal(row.budget_inr),
            holdout_pct=Decimal(row.holdout_pct),
            risk_scenario=row.risk_scenario,
        ),
        campaign_costs_inr={
            t: (
                DEFAULT_CAMPAIGN_COSTS[t]
                if t not in costs  # saved before this cost type existed
                else None
                if costs[t] is None
                else Decimal(str(costs[t]))
            )
            for t in COST_TYPES
        },
        note=row.note,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _latest_row(session: Session, merchant_id: str) -> MerchantSetting | None:
    return session.scalar(
        select(MerchantSetting)
        .where(MerchantSetting.merchant_id == merchant_id)
        .order_by(MerchantSetting.policy_version.desc())
        .limit(1)
    )


def make_lookup(bind: Engine) -> Callable[[str], SavedSettings | None]:
    """A lookup that reads on its own short connection, so it never disturbs the caller's
    transaction (services call merchant_policy both inside and outside transactions)."""

    def lookup(merchant_id: str) -> SavedSettings | None:
        with Session(bind) as s:
            row = _latest_row(s, merchant_id)
            return None if row is None else _to_saved(row)

    return lookup


def _out(
    session: Session, repo: DataRepository, merchant_id: str, changed: bool = False
) -> SettingsOut:
    saved = repo.saved_settings(merchant_id)
    prefs = {k: p.value for k, p in current_preferences(session, merchant_id).items()}
    costs = saved.campaign_costs_inr if saved is not None else DEFAULT_CAMPAIGN_COSTS
    return SettingsOut(
        merchant_id=merchant_id,
        source="merchant" if saved is not None else "default",
        policy=policy_out(repo.merchant_policy(merchant_id)),
        default_policy=policy_out(repo.default_policy(merchant_id)),
        campaign_costs_inr={k: (None if v is None else float(v)) for k, v in costs.items()},
        default_campaign_costs_inr={
            k: (None if v is None else float(v)) for k, v in DEFAULT_CAMPAIGN_COSTS.items()
        },
        preferences=prefs,
        updated_at=None if saved is None else saved.created_at,
        updated_by=None if saved is None else saved.created_by,
        note=None if saved is None else saved.note,
        changed=changed,
    )


def get_settings_out(session: Session, repo: DataRepository, merchant_id: str) -> SettingsOut:
    repo.default_policy(merchant_id)  # 404 for unknown merchants
    with session.begin():
        return _out(session, repo, merchant_id)


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f") if d == d.to_integral() else format(d, "f")


def save_settings(
    session: Session, repo: DataRepository, merchant_id: str, body: SettingsIn
) -> SettingsOut:
    current = repo.merchant_policy(merchant_id)  # raises UnknownMerchant
    saved = repo.saved_settings(merchant_id)
    current_costs = saved.campaign_costs_inr if saved is not None else DEFAULT_CAMPAIGN_COSTS
    new_costs = {**current_costs, **body.campaign_costs_inr}

    limits_now: dict[str, Any] = (
        {}
        if current is None
        else {
            "discount_cap_pct": current.discount_cap_pct,
            "min_margin_pct": current.min_margin_pct,
            "max_loss_inr": current.max_loss_inr,
            "budget_inr": current.budget_inr,
            "holdout_pct": int(current.holdout_pct),
            "risk_scenario": current.risk_scenario,
        }
    )
    limits_new: dict[str, Any] = {
        "discount_cap_pct": body.discount_cap_pct,
        "min_margin_pct": body.min_margin_pct,
        "max_loss_inr": body.max_loss_inr,
        "budget_inr": body.budget_inr,
        "holdout_pct": body.holdout_pct,
        "risk_scenario": body.risk_scenario,
    }
    changed_limits = {k for k, v in limits_new.items() if limits_now.get(k) != v}
    changed_costs = {t for t in COST_TYPES if current_costs.get(t) != new_costs.get(t)}
    needs_version = bool(changed_limits or changed_costs) or (saved is None and current is None)

    with session.begin():
        prefs_now = {k: p.value for k, p in current_preferences(session, merchant_id).items()}
        wanted = body.preferences.model_dump()
        pref_changes: dict[str, str] = {}
        for key in PREF_KEYS:
            value = "" if wanted[key] is None else str(wanted[key])
            if prefs_now.get(key, "") != value:
                pref_changes[key] = value
                session.add(
                    MerchantPreference(
                        merchant_id=merchant_id,
                        key=key,
                        value=value,
                        source="merchant_settings",
                        campaign_version_id=None,
                        created_by=body.updated_by,
                    )
                )
        version = None
        if needs_version:
            version = (current.policy_version if current is not None else 0) + 1
            session.add(
                MerchantSetting(
                    merchant_id=merchant_id,
                    policy_version=version,
                    discount_cap_pct=_fmt(body.discount_cap_pct),
                    min_margin_pct=_fmt(body.min_margin_pct),
                    max_loss_inr=_fmt(body.max_loss_inr),
                    budget_inr=_fmt(body.budget_inr),
                    holdout_pct=body.holdout_pct,
                    risk_scenario=body.risk_scenario,
                    campaign_costs_inr={
                        t: (None if new_costs.get(t) is None else _fmt(new_costs[t]))  # type: ignore[arg-type]
                        for t in COST_TYPES
                    },
                    note=body.note,
                    created_by=body.updated_by,
                )
            )
        if version is not None or pref_changes:
            session.add(
                AuditEvent(
                    actor=body.updated_by,
                    event_type="settings_updated",
                    merchant_id=merchant_id,
                    campaign_id=None,
                    payload={
                        "policy_version": version,
                        "changed_limits": sorted(changed_limits),
                        "changed_costs": sorted(changed_costs),
                        "changed_preferences": {k: v or None for k, v in pref_changes.items()},
                    },
                )
            )
    with session.begin():
        return _out(session, repo, merchant_id, changed=version is not None or bool(pref_changes))
