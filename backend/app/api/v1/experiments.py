"""Simulated execution and measurement routes (M8).

- POST /campaigns/{id}/versions/{n}/execute   (Idempotency-Key header required)
    201 new simulated experiment; 200 replay of the same key (replayed=true).
    409 not_approved | stale_version | policy_blocked | already_executed |
        idempotency_key_reused;  422 insufficient_data | policy_missing.
- GET  /campaigns/{id}/experiments
- GET  /experiments/{experiment_id}
- GET  /experiments/{experiment_id}/assignments?group=&limit=&offset=

Every response is labelled is_simulated=true. Nothing contacts Paytm or any real system.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import EffectiveRepo
from app.db import get_session
from app.schemas.experiments import AssignmentPage, ExecuteRequest, ExperimentOut
from app.services import execution as svc

router = APIRouter(tags=["experiments"])

RepoDep = EffectiveRepo
SessionDep = Annotated[Session, Depends(get_session)]


@router.post(
    "/campaigns/{campaign_id}/versions/{version}/execute",
    response_model=ExperimentOut,
    status_code=201,
    responses={200: {"description": "Replay of an earlier request with the same key."}},
)
def execute(
    campaign_id: uuid.UUID,
    version: int,
    response: Response,
    repo: RepoDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    body: ExecuteRequest | None = None,
) -> ExperimentOut:
    """Run the approved current version in SIMULATION and measure it against a holdout."""
    experiment_id, replayed = svc.execute(
        session, repo, campaign_id, version, idempotency_key, body or ExecuteRequest()
    )
    if replayed:
        response.status_code = 200
    return svc.get_experiment(session, experiment_id, repo.data_source, replayed=replayed)


@router.get("/campaigns/{campaign_id}/experiments", response_model=list[ExperimentOut])
def campaign_experiments(
    campaign_id: uuid.UUID, repo: RepoDep, session: SessionDep
) -> list[ExperimentOut]:
    return svc.experiments_for_campaign(session, campaign_id, repo.data_source)


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: uuid.UUID, repo: RepoDep, session: SessionDep) -> ExperimentOut:
    return svc.get_experiment(session, experiment_id, repo.data_source)


@router.get("/experiments/{experiment_id}/assignments", response_model=AssignmentPage)
def assignments(
    experiment_id: uuid.UUID,
    session: SessionDep,
    group: Literal["treatment", "holdout"] | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssignmentPage:
    return svc.assignments_page(session, experiment_id, group, limit, offset)
