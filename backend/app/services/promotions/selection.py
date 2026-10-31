"""The merchant's product selection: which products may appear in offers.

Append-only versions in merchant_product_selections. Until the first save every product
is allowed. Saving the same set again creates nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.data import DataRepository
from app.db.models import AuditEvent, MerchantProductSelection
from app.schemas.promotions import CatalogueOut, SelectionIn
from app.services.campaign_store import CampaignError
from app.services.promotions.catalogue import catalogue_out


class UnknownProducts(CampaignError):
    http_status, error_code = 422, "unknown_products"


def _latest(session: Session, merchant_id: str) -> MerchantProductSelection | None:
    return session.scalar(
        select(MerchantProductSelection)
        .where(MerchantProductSelection.merchant_id == merchant_id)
        .order_by(MerchantProductSelection.version.desc())
        .limit(1)
    )


def make_selection_lookup(bind: Engine) -> Callable[[str], tuple[int, list[str]] | None]:
    def lookup(merchant_id: str) -> tuple[int, list[str]] | None:
        with Session(bind) as s:
            row = _latest(s, merchant_id)
            return None if row is None else (row.version, list(row.skus))

    return lookup


def save_selection(
    session: Session, repo: DataRepository, merchant_id: str, body: SelectionIn
) -> tuple[CatalogueOut, bool]:
    known = list(repo.products_for(merchant_id)["sku"])  # raises UnknownMerchant
    unknown = sorted(set(body.skus) - set(known))
    if unknown:
        raise UnknownProducts(
            f"Unknown product(s) for this merchant: {', '.join(unknown)}.", {"skus": unknown}
        )
    wanted = [s for s in known if s in set(body.skus)]
    _, _, current = repo.product_selection(merchant_id)
    changed = wanted != current
    if changed:
        with session.begin():
            row = _latest(session, merchant_id)
            new_version = 1 if row is None else row.version + 1
            session.add(
                MerchantProductSelection(
                    merchant_id=merchant_id,
                    version=new_version,
                    skus=wanted,
                    note=body.note,
                    created_by=body.updated_by,
                )
            )
            session.add(
                AuditEvent(
                    actor=body.updated_by,
                    event_type="product_selection_updated",
                    merchant_id=merchant_id,
                    campaign_id=None,
                    payload={
                        "version": new_version,
                        "added": sorted(set(wanted) - set(current)),
                        "removed": sorted(set(current) - set(wanted)),
                    },
                )
            )
    return catalogue_out(repo, merchant_id), changed
