from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.classification_agent import run_classifier
from agents.intake_agent import run_intake
from agents.monitoring_agent import logger
from agents.relationship_graph_agent import run_relationship_graph
from agents.response_agent import run_response
from agents.schedule_agent import run_schedule
from config import API_TITLE, API_VERSION, OUMA_SCHEMA_VERSION
from db import SessionLocal, init_db
from models import AgentRun, ClassifierResult, ReplySuggestion, ScheduleCandidate
from repositories import (
    create_agent_run,
    finalize_agent_run_failed,
    finalize_agent_run_success,
    get_current_classifier,
    get_current_top_schedule_candidate,
    get_latest_branch_statuses,
)
from schemas import AgentRunStatus, OUMAEnvelope

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=API_TITLE,
    description="OUMA v2 aligned API for n8n orchestration",
    version=API_VERSION,
    lifespan=lifespan,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _envelope_response(env: OUMAEnvelope, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": OUMA_SCHEMA_VERSION,
        "trace_id": env.trace_id,
        "run_id": env.run_id,
        "email_id": env.email_id,
        "user_id": env.user_id,
        "agent_name": env.agent_name.value,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def _start_run(session: Session, env: OUMAEnvelope) -> None:
    create_agent_run(
        session,
        run_id=env.run_id,
        trace_id=env.trace_id,
        email_id=env.email_id,
        user_id=env.user_id,
        agent_name=env.agent_name.value,
        input_payload=env.payload,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "schema_version": OUMA_SCHEMA_VERSION}


@app.post("/v2/intake/email", response_model=dict)
def intake_email(env: OUMAEnvelope, db: Session = Depends(get_db)):
    if env.agent_name.value != "intake":
        raise HTTPException(status_code=400, detail="agent_name must be intake")

    try:
        _start_run(db, env)
        output = run_intake(
            db,
            trace_id=env.trace_id,
            run_id=env.run_id,
            email_id=env.email_id,
            user_id=env.user_id,
            payload=env.payload,
        )
        finalize_agent_run_success(db, env.run_id, output)
        db.commit()
        return _envelope_response(env, output)
    except Exception as exc:
        db.rollback()
        logger.exception("intake failed")
        try:
            finalize_agent_run_failed(db, env.run_id, "INTAKE_ERROR", str(exc))
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v2/agents/classifier/run", response_model=dict)
def classifier_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    if env.agent_name.value != "classifier":
        raise HTTPException(status_code=400, detail="agent_name must be classifier")

    try:
        _start_run(db, env)
        output = run_classifier(
            db,
            trace_id=env.trace_id,
            run_id=env.run_id,
            email_id=env.email_id,
            user_id=env.user_id,
        )
        finalize_agent_run_success(db, env.run_id, output)
        db.commit()
        return _envelope_response(env, output)
    except Exception as exc:
        db.rollback()
        logger.exception("classifier failed")
        try:
            finalize_agent_run_failed(db, env.run_id, "CLASSIFIER_ERROR", str(exc))
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v2/agents/relationship_graph/run", response_model=dict)
def relationship_graph_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    if env.agent_name.value != "relationship_graph":
        raise HTTPException(status_code=400, detail="agent_name must be relationship_graph")

    try:
        _start_run(db, env)
        output = run_relationship_graph(
            db,
            trace_id=env.trace_id,
            run_id=env.run_id,
            email_id=env.email_id,
            user_id=env.user_id,
        )
        finalize_agent_run_success(db, env.run_id, output)
        db.commit()
        return _envelope_response(env, output)
    except Exception as exc:
        db.rollback()
        logger.exception("relationship_graph failed")
        try:
            finalize_agent_run_failed(db, env.run_id, "RELATIONSHIP_GRAPH_ERROR", str(exc))
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v2/agents/schedule/run", response_model=dict)
def schedule_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    if env.agent_name.value != "schedule":
        raise HTTPException(status_code=400, detail="agent_name must be schedule")

    try:
        _start_run(db, env)
        output = run_schedule(
            db,
            trace_id=env.trace_id,
            run_id=env.run_id,
            email_id=env.email_id,
            user_id=env.user_id,
        )
        finalize_agent_run_success(db, env.run_id, output)
        db.commit()
        return _envelope_response(env, output)
    except Exception as exc:
        db.rollback()
        logger.exception("schedule failed")
        try:
            finalize_agent_run_failed(db, env.run_id, "SCHEDULE_ERROR", str(exc))
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v2/agents/response/run", response_model=dict)
def response_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    if env.agent_name.value != "response":
        raise HTTPException(status_code=400, detail="agent_name must be response")

    required_branches = ["attachment", "relationship_graph", "schedule"]
    statuses = get_latest_branch_statuses(db, env.trace_id, env.email_id, required_branches)
    for name, status in statuses.items():
        if status not in {AgentRunStatus.success.value, AgentRunStatus.skipped.value}:
            raise HTTPException(
                status_code=409,
                detail=f"response blocked: branch '{name}' status is '{status}'",
            )

    try:
        _start_run(db, env)
        attachment_status = statuses["attachment"] or AgentRunStatus.skipped.value
        output = run_response(
            db,
            trace_id=env.trace_id,
            run_id=env.run_id,
            email_id=env.email_id,
            user_id=env.user_id,
            attachment_status=attachment_status,
        )
        finalize_agent_run_success(db, env.run_id, output)
        db.commit()
        return _envelope_response(env, output)
    except Exception as exc:
        db.rollback()
        logger.exception("response failed")
        try:
            finalize_agent_run_failed(db, env.run_id, "RESPONSE_ERROR", str(exc))
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/v2/traces/{trace_id}/emails/{email_id}/status", response_model=dict)
def trace_email_status(trace_id: str, email_id: str, db: Session = Depends(get_db)):
    statuses = get_latest_branch_statuses(
        db,
        trace_id=trace_id,
        email_id=email_id,
        agents=["classifier", "attachment", "relationship_graph", "schedule", "response"],
    )
    classifier = get_current_classifier(db, email_id)
    top_candidate = get_current_top_schedule_candidate(db, email_id)
    response = db.scalars(
        select(ReplySuggestion)
        .where(ReplySuggestion.email_id == email_id, ReplySuggestion.is_current.is_(True))
        .order_by(ReplySuggestion.created_at_utc.desc())
        .limit(1)
    ).first()

    return {
        "trace_id": trace_id,
        "email_id": email_id,
        "branch_statuses": statuses,
        "current_classifier": {
            "category": classifier.category,
            "urgency_score": classifier.urgency_score,
            "summary": classifier.summary,
            "sender_role": classifier.sender_role,
            "named_entities": classifier.named_entities,
            "time_expressions": classifier.time_expressions,
        }
        if classifier
        else None,
        "top_schedule_candidate": {
            "candidate_id": top_candidate.candidate_id,
            "title": top_candidate.title,
            "action": top_candidate.action,
            "transaction_id": top_candidate.transaction_id,
            "write_status": top_candidate.write_status,
            "outlook_event_id": top_candidate.outlook_event_id,
        }
        if top_candidate
        else None,
        "current_response": {
            "reply_required": response.reply_required,
            "decision_reason": response.decision_reason,
            "tone_templates": response.tone_templates,
        }
        if response
        else None,
    }
