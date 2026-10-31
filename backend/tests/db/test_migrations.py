"""Migration reversibility, model parity and database-level constraints."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Campaign,
    CampaignApproval,
    CampaignEvaluation,
    CampaignVersion,
    ImmutableRecordError,
)
from tests.db.conftest import alembic_config

TABLES = {
    "campaigns",
    "campaign_versions",
    "campaign_evaluations",
    "campaign_approvals",
    "audit_events",
    "recommendation_runs",
    "executions",
    "assignments",
    "outcomes",
    "measurements",
}
HASH = "a" * 64


def test_migration_matches_models(engine: Engine) -> None:
    assert models.APPEND_ONLY_TABLES  # models registered
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == []
    assert TABLES <= set(inspect(engine).get_table_names())


def test_migration_is_reversible(db_url: str, engine: Engine) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    assert not TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(engine).get_table_names())


def _seed(s: Session) -> tuple[Campaign, CampaignVersion]:
    c = Campaign(
        id=uuid.uuid4(),
        merchant_id="M001",
        campaign_type="targeted_offer",
        current_version=1,
        execution_status="not_executed",
        data_source="test",
        is_synthetic=True,
        created_by="t",
    )
    v = CampaignVersion(
        id=uuid.uuid4(),
        campaign_id=c.id,
        version=1,
        input_hash=HASH,
        inputs={},
        assumption_source="illustrative",
        created_by="t",
    )
    s.add(c)
    s.flush()
    s.add(v)
    s.flush()
    return c, v


def _expect_integrity(sessions: sessionmaker[Session], build: object) -> None:
    with sessions() as s, pytest.raises(IntegrityError), s.begin():
        c, v = _seed(s)
        s.add(build(c, v))  # type: ignore[operator]
        s.flush()


def test_duplicate_version_number_rejected(sessions: sessionmaker[Session]) -> None:
    _expect_integrity(
        sessions,
        lambda c, v: CampaignVersion(
            campaign_id=c.id,
            version=1,
            input_hash="b" * 64,
            inputs={},
            assumption_source="x",
            created_by="t",
        ),
    )


def test_approval_hash_must_match_version(sessions: sessionmaker[Session]) -> None:
    _expect_integrity(
        sessions,
        lambda c, v: CampaignApproval(
            campaign_version_id=v.id, input_hash="b" * 64, decision="approved", decided_by="t"
        ),
    )


def test_one_decision_per_version(sessions: sessionmaker[Session]) -> None:
    with sessions() as s, pytest.raises(IntegrityError), s.begin():
        c, v = _seed(s)
        for d in ("approved", "rejected"):
            s.add(
                CampaignApproval(
                    campaign_version_id=v.id, input_hash=HASH, decision=d, decided_by="t"
                )
            )
            s.flush()


@pytest.mark.parametrize(
    "build",
    [
        lambda c, v: CampaignApproval(
            campaign_version_id=v.id, input_hash=HASH, decision="maybe", decided_by="t"
        ),
        lambda c, v: CampaignEvaluation(
            campaign_version_id=v.id,
            trigger="version_created",
            policy_version=1,
            economics_status="ok",
            permitted=False,
            executable=True,
            economics={},
            policy_decision={},
        ),
        lambda c, v: CampaignEvaluation(
            campaign_version_id=v.id,
            trigger="manual",
            policy_version=1,
            economics_status="ok",
            permitted=True,
            executable=False,
            economics={},
            policy_decision={},
        ),
    ],
    ids=["bad_decision", "executable_without_permitted", "bad_trigger"],
)
def test_check_constraints(sessions: sessionmaker[Session], build: object) -> None:
    _expect_integrity(sessions, build)


def test_execution_status_cannot_claim_execution(sessions: sessionmaker[Session]) -> None:
    with sessions() as s, pytest.raises(IntegrityError), s.begin():
        c, _ = _seed(s)
        c.execution_status = "executed"
        s.flush()


@pytest.mark.parametrize("table", ["audit_events", "campaign_versions"])
@pytest.mark.parametrize("sql", ["UPDATE {t} SET {col} = {col}", "DELETE FROM {t}"])
def test_append_only_enforced_by_database(
    sessions: sessionmaker[Session], table: str, sql: str
) -> None:
    with sessions() as s, s.begin():
        c, v = _seed(s)
        s.add(
            AuditEvent(actor="t", event_type="x", merchant_id="M001", campaign_id=c.id, payload={})
        )
    col = "actor" if table == "audit_events" else "created_by"
    with sessions() as s, pytest.raises(DBAPIError, match="append-only"), s.begin():
        s.execute(text(sql.format(t=table, col=col)))


def test_append_only_enforced_by_orm(sessions: sessionmaker[Session]) -> None:
    with sessions() as s, s.begin():
        _, v = _seed(s)
    with sessions() as s, pytest.raises(ImmutableRecordError), s.begin():
        row = s.get(CampaignVersion, v.id)
        assert row is not None
        row.change_note = "rewritten"
        s.flush()


def test_downgrade_0003_refuses_to_drop_simulated_executions(
    db_url: str, engine: Engine, sessions: sessionmaker[Session]
) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "0002_recommendations_preferences")  # empty: reversible
    assert "executions" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    with sessions() as s, s.begin():
        c, _ = _seed(s)
        c.execution_status = "simulated"
    engine.dispose()
    with pytest.raises(RuntimeError, match="simulated"):
        command.downgrade(cfg, "0002_recommendations_preferences")
    assert "executions" in inspect(engine).get_table_names()
