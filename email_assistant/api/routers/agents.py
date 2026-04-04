from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db
from config import OUMA_SCHEMA_VERSION
from schemas import OUMAEnvelope
from services.agent_run_service import envelope_response, run_agent_envelope

router = APIRouter(tags=["agents"])


def _run(env: OUMAEnvelope, expected_name: str, db: Session) -> dict[str, object]:
    if env.agent_name.value != expected_name:
        raise HTTPException(status_code=400, detail=f"agent_name must be {expected_name}")
    payload = run_agent_envelope(db, env)
    return envelope_response(env, payload, schema_version=OUMA_SCHEMA_VERSION)


@router.post("/v2/intake/email", response_model=dict)
def intake_email(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "intake", db)


@router.post("/v2/agents/classifier/run", response_model=dict)
def classifier_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "classifier", db)


@router.post("/v2/agents/relationship_graph/run", response_model=dict)
def relationship_graph_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "relationship_graph", db)


@router.post("/v2/agents/schedule/run", response_model=dict)
def schedule_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "schedule", db)


@router.post("/v2/agents/response/run", response_model=dict)
def response_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "response", db)
