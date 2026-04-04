from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from config import ATTACHMENTS_DIR
from models import (
    AgentRun,
    Attachment,
    AttachmentResult,
    CategoryDefinition,
    ClassifierResult,
    Email,
    EmailRecipient,
    RelationshipObservation,
    ReplySuggestion,
    ScheduleCandidate,
    User,
)
from schemas import AgentRunStatus, AttachmentPayload, EmailPayload, EmailRecipientPayload, UserPayload


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_filename(filename: str) -> str:
    keep = "".join(ch for ch in filename if ch.isalnum() or ch in (".", "_", "-"))
    return keep or "attachment.bin"


def store_attachment_content(attachment_id: str, name: str, content_base64: Optional[str]) -> Optional[str]:
    if not content_base64:
        return None

    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(name)
    output_path = ATTACHMENTS_DIR / f"{attachment_id}_{safe_name}"
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(content_base64))
    return str(output_path)


def upsert_user(session: Session, user: UserPayload) -> User:
    existing = session.get(User, user.user_id)
    if existing is None:
        existing = User(
            user_id=user.user_id,
            primary_email=user.primary_email,
            display_name=user.display_name,
            timezone=user.timezone,
        )
        session.add(existing)
    else:
        existing.primary_email = user.primary_email or existing.primary_email
        existing.display_name = user.display_name or existing.display_name
        existing.timezone = user.timezone or existing.timezone
    return existing


def upsert_email(session: Session, user_id: str, email: EmailPayload) -> Email:
    existing = session.get(Email, email.email_id)
    if existing is None:
        existing = Email(
            email_id=email.email_id,
            user_id=user_id,
            graph_message_id=email.graph_message_id,
            graph_immutable_id=email.graph_immutable_id,
            internet_message_id=email.internet_message_id,
            conversation_id=email.conversation_id,
            sender_name=email.sender_name,
            sender_email=email.sender_email,
            subject=email.subject,
            body_content_type=email.body_content_type,
            body_content=email.body_content,
            body_preview=email.body_preview,
            received_at_utc=ensure_utc(email.received_at_utc),
            has_attachments=email.has_attachments,
        )
        session.add(existing)
    else:
        existing.user_id = user_id
        existing.graph_message_id = email.graph_message_id
        existing.graph_immutable_id = email.graph_immutable_id
        existing.internet_message_id = email.internet_message_id
        existing.conversation_id = email.conversation_id
        existing.sender_name = email.sender_name
        existing.sender_email = email.sender_email
        existing.subject = email.subject
        existing.body_content_type = email.body_content_type
        existing.body_content = email.body_content
        existing.body_preview = email.body_preview
        existing.received_at_utc = ensure_utc(email.received_at_utc)
        existing.has_attachments = email.has_attachments
    return existing


def replace_recipients(session: Session, email_id: str, recipients: list[EmailRecipientPayload]) -> None:
    session.query(EmailRecipient).filter(EmailRecipient.email_id == email_id).delete()
    for r in recipients:
        session.add(
            EmailRecipient(
                email_id=email_id,
                recipient_email=r.recipient_email,
                recipient_name=r.recipient_name,
                recipient_type=r.recipient_type,
            )
        )


def upsert_attachments(session: Session, email_id: str, attachments: list[AttachmentPayload]) -> list[Attachment]:
    saved: list[Attachment] = []
    for item in attachments:
        local_path = item.local_path
        if not local_path:
            local_path = store_attachment_content(item.attachment_id, item.name, item.content_base64)

        existing = session.get(Attachment, item.attachment_id)
        if existing is None:
            existing = Attachment(
                attachment_id=item.attachment_id,
                email_id=email_id,
                graph_attachment_id=item.graph_attachment_id,
                name=item.name,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                is_inline=item.is_inline,
                local_path=local_path,
            )
            session.add(existing)
        else:
            existing.email_id = email_id
            existing.graph_attachment_id = item.graph_attachment_id
            existing.name = item.name
            existing.content_type = item.content_type
            existing.size_bytes = item.size_bytes
            existing.is_inline = item.is_inline
            existing.local_path = local_path or existing.local_path
        saved.append(existing)
    return saved


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


def set_non_current_classifier(session: Session, email_id: str) -> None:
    session.execute(
        update(ClassifierResult)
        .where(ClassifierResult.email_id == email_id, ClassifierResult.is_current.is_(True))
        .values(is_current=False)
    )


def set_non_current_attachment(session: Session, attachment_id: str) -> None:
    session.execute(
        update(AttachmentResult)
        .where(AttachmentResult.attachment_id == attachment_id, AttachmentResult.is_current.is_(True))
        .values(is_current=False)
    )


def set_non_current_schedule(session: Session, email_id: str) -> None:
    session.execute(
        update(ScheduleCandidate)
        .where(ScheduleCandidate.email_id == email_id, ScheduleCandidate.is_current.is_(True))
        .values(is_current=False)
    )


def set_non_current_reply(session: Session, email_id: str) -> None:
    session.execute(
        update(ReplySuggestion)
        .where(ReplySuggestion.email_id == email_id, ReplySuggestion.is_current.is_(True))
        .values(is_current=False)
    )


def get_email(session: Session, email_id: str) -> Optional[Email]:
    return session.get(Email, email_id)


def get_email_attachments(session: Session, email_id: str) -> list[Attachment]:
    return session.scalars(select(Attachment).where(Attachment.email_id == email_id)).all()


def get_current_classifier(session: Session, email_id: str) -> Optional[ClassifierResult]:
    return session.scalars(
        select(ClassifierResult)
        .where(ClassifierResult.email_id == email_id, ClassifierResult.is_current.is_(True))
        .order_by(ClassifierResult.created_at_utc.desc())
        .limit(1)
    ).first()


def get_current_attachment_results(session: Session, email_id: str) -> list[AttachmentResult]:
    return session.scalars(
        select(AttachmentResult)
        .where(AttachmentResult.email_id == email_id, AttachmentResult.is_current.is_(True))
        .order_by(AttachmentResult.created_at_utc.desc())
    ).all()


def get_current_top_schedule_candidate(session: Session, email_id: str) -> Optional[ScheduleCandidate]:
    return session.scalars(
        select(ScheduleCandidate)
        .where(ScheduleCandidate.email_id == email_id, ScheduleCandidate.is_current.is_(True))
        .order_by(
            func.coalesce(ScheduleCandidate.recommendation_rank, 9999).asc(),
            ScheduleCandidate.confidence.desc(),
            ScheduleCandidate.created_at_utc.desc(),
        )
        .limit(1)
    ).first()


def get_latest_branch_statuses(session: Session, trace_id: str, email_id: str, agents: list[str]) -> dict[str, Optional[str]]:
    statuses: dict[str, Optional[str]] = {}
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


def get_relationship_snapshot(session: Session, email_id: str) -> Optional[dict[str, Any]]:
    email = get_email(session, email_id)
    if email is None:
        return None

    sender_email = email.sender_email
    if not sender_email:
        return None

    count = session.scalar(
        select(func.count(RelationshipObservation.id)).where(
            RelationshipObservation.email_id == email_id,
            RelationshipObservation.person_email == sender_email,
        )
    )
    weight = min(1.0, float(count or 0) / 10.0 + 0.5)
    last_obs = session.scalars(
        select(RelationshipObservation)
        .where(RelationshipObservation.email_id == email_id, RelationshipObservation.person_email == sender_email)
        .order_by(RelationshipObservation.created_at_utc.desc())
        .limit(1)
    ).first()

    return {
        "sender_email": sender_email,
        "relationship_weight": round(weight, 4),
        "sender_role": last_obs.person_role if last_obs else None,
    }


def path_exists(path: Optional[str]) -> bool:
    if not path:
        return False
    return Path(path).exists()


def get_category_definitions(session: Session, user_id: str) -> list[CategoryDefinition]:
    return session.scalars(
        select(CategoryDefinition)
        .where(CategoryDefinition.user_id == user_id)
        .order_by(CategoryDefinition.created_at_utc.asc())
    ).all()


def get_category_by_name(session: Session, user_id: str, category_name: str) -> Optional[CategoryDefinition]:
    return session.scalars(
        select(CategoryDefinition)
        .where(
            CategoryDefinition.user_id == user_id,
            CategoryDefinition.category_name == category_name,
        )
        .limit(1)
    ).first()


def create_category_definition(
    session: Session,
    *,
    user_id: str,
    category_name: str,
    category_description: str,
    created_from_email_id: Optional[str] = None,
) -> CategoryDefinition:
    existing = get_category_by_name(session, user_id, category_name)
    if existing:
        return existing
    category = CategoryDefinition(
        category_id=str(uuid4()),
        user_id=user_id,
        category_name=category_name,
        category_description=category_description,
        created_from_email_id=created_from_email_id,
    )
    session.add(category)
    session.flush()
    return category
