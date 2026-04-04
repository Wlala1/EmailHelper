from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from agents.classification_agent import run_classifier
from agents.intake_agent import run_intake
from agents.monitoring_agent import logger
from agents.relationship_graph_agent import run_relationship_graph
from agents.response_agent import run_response
from agents.schedule_agent import run_schedule
from repositories import (
    create_agent_run,
    finalize_agent_run_failed,
    finalize_agent_run_success,
    get_latest_branch_statuses,
)
from schemas import AgentRunStatus, OUMAEnvelope

AgentRunner = Callable[..., dict[str, Any]]


RUNNERS: dict[str, tuple[AgentRunner, str]] = {
    "intake": (run_intake, "INTAKE_ERROR"),
    "classifier": (run_classifier, "CLASSIFIER_ERROR"),
    "relationship_graph": (run_relationship_graph, "RELATIONSHIP_GRAPH_ERROR"),
    "schedule": (run_schedule, "SCHEDULE_ERROR"),
    "response": (run_response, "RESPONSE_ERROR"),
}


def envelope_response(env: OUMAEnvelope, payload: dict[str, Any], *, schema_version: str) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "trace_id": env.trace_id,
        "run_id": env.run_id,
        "email_id": env.email_id,
        "user_id": env.user_id,
        "agent_name": env.agent_name.value,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def ensure_response_ready(session: Session, *, trace_id: str, email_id: str) -> str:
    required_branches = ["attachment", "relationship_graph", "schedule"]
    statuses = get_latest_branch_statuses(session, trace_id, email_id, required_branches)
    for name, status in statuses.items():
        if status not in {AgentRunStatus.success.value, AgentRunStatus.skipped.value}:
            raise HTTPException(status_code=409, detail=f"response blocked: branch '{name}' status is '{status}'")
    return statuses["attachment"] or AgentRunStatus.skipped.value


def run_agent_envelope(session: Session, env: OUMAEnvelope) -> dict[str, Any]:
    expected_name = env.agent_name.value
    runner_entry = RUNNERS.get(expected_name)
    if runner_entry is None:
        raise HTTPException(status_code=400, detail=f"unsupported agent_name: {expected_name}")

    runner, error_code = runner_entry
    runner_kwargs: dict[str, Any] = {}
    if expected_name == "intake":
        runner_kwargs["payload"] = env.payload
    elif expected_name == "response":
        runner_kwargs["attachment_status"] = ensure_response_ready(session, trace_id=env.trace_id, email_id=env.email_id)

    try:
        create_agent_run(
            session,
            run_id=env.run_id,
            trace_id=env.trace_id,
            email_id=env.email_id,
            user_id=env.user_id,
            agent_name=expected_name,
            input_payload=env.payload,
        )
        output = runner(
            session,
            trace_id=env.trace_id,
            run_id=env.run_id,
            email_id=env.email_id,
            user_id=env.user_id,
            **runner_kwargs,
        )
        finalize_agent_run_success(session, env.run_id, output)
        session.commit()
        return output
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        logger.exception("%s failed", expected_name)
        try:
            finalize_agent_run_failed(session, env.run_id, error_code, str(exc))
            session.commit()
        except Exception:
            session.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
