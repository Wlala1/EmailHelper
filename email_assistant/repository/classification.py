from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
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
    UserFeedbackEvent,
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


def get_relationship_snapshot(
    session: Session,
    email_id: str,
    *,
    decay_half_life_days: float = 90.0,
) -> dict[str, Any] | None:
    """Return relationship context for the sender of the given email.

    When USE_DECAYED_WEIGHT=true (Phase B), the weight is calculated as a
    time-decayed sum of all signal weights (90-day half-life by default).
    The legacy formula (min(1.0, count/10 + 0.5)) is always included as
    `relationship_weight` for backward compatibility.

    Returns keys:
      sender_email, relationship_weight (legacy), decayed_weight,
      sender_role, org_name, org_domain, observation_count,
      source_timezone (None unless set on user).
    """
    from config import USE_DECAYED_WEIGHT

    email = session.get(Email, email_id)
    if email is None or not email.sender_email:
        return None

    observations = session.scalars(
        select(RelationshipObservation)
        .where(
            RelationshipObservation.user_id == email.user_id,
            RelationshipObservation.person_email == email.sender_email,
        )
        .order_by(RelationshipObservation.created_at_utc.desc())
    ).all()

    count = len(observations)
    # Legacy weight formula (kept for backward compat).
    legacy_weight = min(1.0, float(count) / 10.0 + 0.5)

    # Time-decayed weight: each observation contributes signal_weight * exp(-λ * days_ago)
    # with λ = ln(2) / half_life so that weight halves every `decay_half_life_days` days.
    now_utc = datetime.now(timezone.utc)
    decay_lambda = math.log(2) / decay_half_life_days
    decayed_sum = 0.0
    for obs in observations:
        obs_time = obs.observed_at_utc
        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=timezone.utc)
        days_ago = max(0.0, (now_utc - obs_time).total_seconds() / 86400.0)
        decayed_sum += obs.signal_weight * math.exp(-decay_lambda * days_ago)
    decayed_weight = min(1.0, round(decayed_sum, 4))

    last_obs = observations[0] if observations else None
    active_weight = decayed_weight if USE_DECAYED_WEIGHT else legacy_weight

    return {
        "sender_email": email.sender_email,
        "relationship_weight": round(active_weight, 4),
        "decayed_weight": decayed_weight,
        "sender_role": last_obs.person_role if last_obs else None,
        "org_name": last_obs.organisation_name if last_obs else None,
        "org_domain": last_obs.organisation_domain if last_obs else None,
        "observation_count": count,
        "source_timezone": None,
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
    category = CategoryDefinition(
        category_id=str(uuid4()),
        user_id=user_id,
        category_name=category_name,
        category_description=category_description,
        created_from_email_id=created_from_email_id,
    )
    try:
        with session.begin_nested():
            session.add(category)
        return category
    except IntegrityError:
        # Concurrent insert won the race; return the existing row.
        return get_category_by_name(session, user_id, category_name)


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


def get_declined_outlook_event_ids(
    session: Session,
    user_id: str,
    *,
    lookback_days: int = 30,
) -> set[str]:
    """Query Outlook for calendar events the user has declined or that were cancelled.

    Returns the set of outlook_event_ids that are in a declined/cancelled state.
    Used to filter out already-rejected events from proactive recommendations.
    Returns an empty set if MS Graph is unavailable.
    """
    try:
        from datetime import timedelta

        from services.mailbox_actions_service import get_recent_calendar_events

        start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        end = datetime.now(timezone.utc) + timedelta(days=7)
        events = get_recent_calendar_events(session, user_id=user_id, start_time_utc=start, end_time_utc=end)
        declined: set[str] = set()
        for ev in events:
            response = (ev.get("responseStatus") or {}).get("response", "")
            if ev.get("isCancelled") or response == "declined":
                eid = ev.get("id")
                if eid:
                    declined.add(eid)
        return declined
    except Exception:
        return set()


def get_unaccepted_high_priority_candidates(
    session: Session,
    user_id: str,
    *,
    min_relationship_weight: float = 0.7,
    lookback_days: int = 30,
    limit: int = 5,
) -> list[ScheduleCandidate]:
    """Return past schedule candidates that remain unresolved and came from
    high-weight contacts, within the lookback window.

    These are surfaced as proactive recommendations — the front end can prompt
    the user to revisit them.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    terminal_feedback_target_ids = {
        row[0]
        for row in session.execute(
            select(UserFeedbackEvent.target_id).where(
                UserFeedbackEvent.user_id == user_id,
                UserFeedbackEvent.target_type == "schedule_candidate",
                UserFeedbackEvent.feedback_signal.in_(("accepted", "rejected")),
                UserFeedbackEvent.created_at_utc >= cutoff,
            )
        ).all()
    }

    candidates = session.scalars(
        select(ScheduleCandidate)
        .where(
            ScheduleCandidate.user_id == user_id,
            ScheduleCandidate.created_at_utc >= cutoff,
            ScheduleCandidate.action.in_(("suggest_only", "create_tentative_event")),
            ScheduleCandidate.write_status.in_(("pending", "written")),
        )
        .order_by(ScheduleCandidate.confidence.desc(), ScheduleCandidate.created_at_utc.desc())
        .limit(limit * 5)
    ).all()

    # Get Outlook-declined event IDs so we don't re-surface already-rejected events.
    declined_outlook_ids = get_declined_outlook_event_ids(session, user_id, lookback_days=lookback_days)

    result = []
    for candidate in candidates:
        if candidate.candidate_id in terminal_feedback_target_ids:
            continue
        # Skip if the user already declined this event in Outlook.
        if candidate.outlook_event_id and candidate.outlook_event_id in declined_outlook_ids:
            continue
        email = session.get(Email, candidate.email_id)
        if email is None:
            continue
        snapshot = get_relationship_snapshot(session, candidate.email_id)
        weight = (snapshot or {}).get("relationship_weight", 0.0)
        if weight >= min_relationship_weight:
            result.append(candidate)
        if len(result) >= limit:
            break
    return result
