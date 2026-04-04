from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import UserFeedbackEvent


def create_feedback_event(
    session: Session,
    *,
    user_id: str,
    email_id: str,
    target_type: str,
    target_id: str,
    feedback_signal: str,
    feedback_metadata: Optional[dict] = None,
) -> UserFeedbackEvent:
    """Persist a single user feedback event and flush to DB."""
    event = UserFeedbackEvent(
        user_id=user_id,
        email_id=email_id,
        target_type=target_type,
        target_id=target_id,
        feedback_signal=feedback_signal,
        feedback_metadata=feedback_metadata or {},
    )
    session.add(event)
    session.flush()
    return event


def get_feedback_events_for_user(
    session: Session,
    user_id: str,
    *,
    target_type: Optional[str] = None,
    limit: int = 200,
) -> list[UserFeedbackEvent]:
    """Return feedback events for a user, newest first."""
    query = select(UserFeedbackEvent).where(UserFeedbackEvent.user_id == user_id)
    if target_type is not None:
        query = query.where(UserFeedbackEvent.target_type == target_type)
    query = query.order_by(UserFeedbackEvent.created_at_utc.desc()).limit(limit)
    return list(session.scalars(query).all())
