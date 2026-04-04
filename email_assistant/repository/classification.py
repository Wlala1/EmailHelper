from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from models import (
    AttachmentResult,
    CategoryDefinition,
    ClassifierResult,
    Email,
    RelationshipObservation,
    ReplyDraftWrite,
    ReplySuggestion,
    ScheduleCandidate,
)


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


def get_current_classifier(session: Session, email_id: str) -> ClassifierResult | None:
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


def get_current_top_schedule_candidate(session: Session, email_id: str) -> ScheduleCandidate | None:
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


def get_relationship_snapshot(session: Session, email_id: str) -> dict[str, Any] | None:
    email = session.get(Email, email_id)
    if email is None or not email.sender_email:
        return None

    count = session.scalar(
        select(func.count(RelationshipObservation.id)).where(
            RelationshipObservation.user_id == email.user_id,
            RelationshipObservation.person_email == email.sender_email,
        )
    )
    weight = min(1.0, float(count or 0) / 10.0 + 0.5)
    last_obs = session.scalars(
        select(RelationshipObservation)
        .where(
            RelationshipObservation.user_id == email.user_id,
            RelationshipObservation.person_email == email.sender_email,
        )
        .order_by(RelationshipObservation.created_at_utc.desc())
        .limit(1)
    ).first()
    return {
        "sender_email": email.sender_email,
        "relationship_weight": round(weight, 4),
        "sender_role": last_obs.person_role if last_obs else None,
    }


def get_category_definitions(session: Session, user_id: str) -> list[CategoryDefinition]:
    return session.scalars(
        select(CategoryDefinition)
        .where(CategoryDefinition.user_id == user_id)
        .order_by(CategoryDefinition.created_at_utc.asc())
    ).all()


def get_category_by_name(session: Session, user_id: str, category_name: str) -> CategoryDefinition | None:
    return session.scalars(
        select(CategoryDefinition)
        .where(CategoryDefinition.user_id == user_id, CategoryDefinition.category_name == category_name)
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
    if existing is not None:
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


def get_current_reply_suggestion(session: Session, email_id: str) -> ReplySuggestion | None:
    return session.scalars(
        select(ReplySuggestion)
        .where(ReplySuggestion.email_id == email_id, ReplySuggestion.is_current.is_(True))
        .order_by(ReplySuggestion.created_at_utc.desc())
        .limit(1)
    ).first()


def create_reply_draft_write(
    session: Session,
    *,
    reply_suggestion_id: Optional[int],
    user_id: str,
    email_id: str,
    policy_name: str,
    draft_status: str,
    outlook_draft_id: Optional[str] = None,
    outlook_web_link: Optional[str] = None,
    error_message: Optional[str] = None,
) -> ReplyDraftWrite:
    write = ReplyDraftWrite(
        reply_suggestion_id=reply_suggestion_id,
        user_id=user_id,
        email_id=email_id,
        policy_name=policy_name,
        draft_status=draft_status,
        outlook_draft_id=outlook_draft_id,
        outlook_web_link=outlook_web_link,
        error_message=error_message,
    )
    session.add(write)
    session.flush()
    return write


def get_latest_reply_draft_write(session: Session, email_id: str) -> ReplyDraftWrite | None:
    return session.scalars(
        select(ReplyDraftWrite)
        .where(ReplyDraftWrite.email_id == email_id)
        .order_by(ReplyDraftWrite.created_at_utc.desc())
        .limit(1)
    ).first()
