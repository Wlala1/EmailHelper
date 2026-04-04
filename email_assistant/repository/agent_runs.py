from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import AgentRun
from schemas import AgentRunStatus
from utils import utcnow


def create_agent_run(
    session: Session,
    *,
    run_id: str,
    trace_id: str,
    email_id: str,
    user_id: str,
    agent_name: str,
    input_payload: dict[str, Any],
    upstream_run_id: Optional[str] = None,
    model_name: Optional[str] = None,
    model_version: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> AgentRun:
    run = AgentRun(
        run_id=run_id,
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        agent_name=agent_name,
        status=AgentRunStatus.started.value,
        upstream_run_id=upstream_run_id,
        model_name=model_name,
        model_version=model_version,
        prompt_version=prompt_version,
        input_payload=input_payload,
        output_payload={},
    )
    session.add(run)
    session.flush()
    return run


def finalize_agent_run_success(session: Session, run_id: str, output_payload: dict[str, Any]) -> None:
    session.execute(
        update(AgentRun)
        .where(AgentRun.run_id == run_id)
        .values(
            status=AgentRunStatus.success.value,
            output_payload=output_payload,
            error_code=None,
            error_message=None,
            updated_at_utc=utcnow(),
        )
    )


def finalize_agent_run_failed(session: Session, run_id: str, error_code: str, error_message: str) -> None:
    session.execute(
        update(AgentRun)
        .where(AgentRun.run_id == run_id)
        .values(
            status=AgentRunStatus.failed.value,
            error_code=error_code,
            error_message=error_message,
            updated_at_utc=utcnow(),
        )
    )


def create_terminal_run(
    session: Session,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
    agent_name: str,
    status: AgentRunStatus,
    input_payload: Optional[dict[str, Any]] = None,
    output_payload: Optional[dict[str, Any]] = None,
    upstream_run_id: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> AgentRun:
    run = AgentRun(
        run_id=str(uuid4()),
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        agent_name=agent_name,
        status=status.value,
        upstream_run_id=upstream_run_id,
        input_payload=input_payload or {},
        output_payload=output_payload or {},
        error_code=error_code,
        error_message=error_message,
    )
    session.add(run)
    return run


def get_latest_branch_statuses(session: Session, trace_id: str, email_id: str, agents: list[str]) -> dict[str, str | None]:
    statuses: dict[str, str | None] = {}
    for agent in agents:
        row = session.scalars(
            select(AgentRun)
            .where(
                AgentRun.trace_id == trace_id,
                AgentRun.email_id == email_id,
                AgentRun.agent_name == agent,
            )
            .order_by(AgentRun.created_at_utc.desc())
            .limit(1)
        ).first()
        statuses[agent] = row.status if row else None
    return statuses
