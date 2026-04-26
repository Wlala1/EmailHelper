from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    AgentRun,
    ClassifierResult,
    Email,
    RelationshipObservation,
    ReplyDraftWrite,
    ScheduleCandidate,
    UserFeedbackEvent,
)
from repositories import (
    get_current_reply_suggestion,
    get_user_writing_profile,
    list_category_suggestions,
)
from repository.classification import list_pending_schedule_candidates
from services.category_suggestion_service import serialize_category_suggestion


def _pending_review_items(session: Session, *, user_id: str) -> list[dict]:
    writes = session.scalars(
        select(ReplyDraftWrite)
        .where(ReplyDraftWrite.user_id == user_id)
        .order_by(ReplyDraftWrite.created_at_utc.desc())
    ).all()
    seen_email_ids: set[str] = set()
    items: list[dict] = []
    for write in writes:
        if write.email_id in seen_email_ids:
            continue
        seen_email_ids.add(write.email_id)
        if write.draft_status != "pending_review":
            continue
        reply = get_current_reply_suggestion(session, write.email_id)
        if reply is None or write.reply_suggestion_id != reply.id:
            continue
        email = session.get(Email, write.email_id)
        if email is None:
            continue
        items.append(
            {
                "email_id": email.email_id,
                "user_id": email.user_id,
                "reply_suggestion_id": reply.id,
                "subject": email.subject,
                "sender_name": email.sender_name,
                "sender_email": email.sender_email,
                "received_at_utc": email.received_at_utc,
                "decision_reason": reply.decision_reason,
                "draft_status": write.draft_status,
            }
        )
        if len(items) >= 10:
            break
    return items


def _top_relationships(session: Session, *, user_id: str) -> list[dict]:
    observations = session.scalars(
        select(RelationshipObservation)
        .where(RelationshipObservation.user_id == user_id)
        .order_by(RelationshipObservation.created_at_utc.desc())
    ).all()
    aggregate: dict[str, dict] = {}
    email_ids_by_person: dict[str, list[str]] = {}
    for obs in observations:
        bucket = aggregate.setdefault(
            obs.person_email,
            {
                "person_email": obs.person_email,
                "person_name": obs.person_name,
                "person_role": obs.person_role,
                "observation_count": 0,
            },
        )
        bucket["observation_count"] += 1
        if not bucket.get("person_name") and obs.person_name:
            bucket["person_name"] = obs.person_name
        if not bucket.get("person_role") and obs.person_role:
            bucket["person_role"] = obs.person_role
        if obs.signal_type == "email_from":
            email_ids_by_person.setdefault(obs.person_email, []).append(obs.email_id)

    # Look up the most recent classifier category per person (sender emails only)
    for person_email, email_ids in email_ids_by_person.items():
        clf = session.scalars(
            select(ClassifierResult)
            .where(
                ClassifierResult.user_id == user_id,
                ClassifierResult.email_id.in_(email_ids),
                ClassifierResult.is_current.is_(True),
            )
            .order_by(ClassifierResult.created_at_utc.desc())
            .limit(1)
        ).first()
        if clf and person_email in aggregate:
            aggregate[person_email]["email_category"] = clf.category

    ranked = sorted(
        aggregate.values(),
        key=lambda item: (item["observation_count"], item["person_email"]),
        reverse=True,
    )[:20]
    for item in ranked:
        item["relationship_weight"] = round(min(1.0, item["observation_count"] / 10.0 + 0.5), 4)
        item.setdefault("email_category", None)
    return ranked


def build_user_dashboard(session: Session, *, user_id: str) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    processed_email_count = int(
        session.execute(
            select(func.count(func.distinct(AgentRun.email_id))).where(
                AgentRun.user_id == user_id,
                AgentRun.agent_name == "classifier",
                AgentRun.status == "success",
                AgentRun.created_at_utc >= week_ago,
            )
        ).scalar()
        or 0
    )
    pending_review_items = _pending_review_items(session, user_id=user_id)
    pending_suggestions = [
        serialize_category_suggestion(item)
        for item in list_category_suggestions(session, user_id=user_id, statuses=["pending"], limit=8)
    ]

    category_distribution = [
        {"label": category, "value": count}
        for category, count in session.execute(
            select(ClassifierResult.category, func.count(ClassifierResult.id))
            .where(
                ClassifierResult.user_id == user_id,
                ClassifierResult.is_current.is_(True),
            )
            .group_by(ClassifierResult.category)
            .order_by(func.count(ClassifierResult.id).desc(), ClassifierResult.category.asc())
            .limit(8)
        ).all()
    ]

    recent_written_count = int(
        session.execute(
            select(func.count(ScheduleCandidate.id)).where(
                ScheduleCandidate.user_id == user_id,
                ScheduleCandidate.write_status == "written",
                ScheduleCandidate.created_at_utc >= week_ago,
            )
        ).scalar()
        or 0
    )
    current_suggest_only_count = int(
        session.execute(
            select(func.count(ScheduleCandidate.id)).where(
                ScheduleCandidate.user_id == user_id,
                ScheduleCandidate.is_current.is_(True),
                ScheduleCandidate.action == "suggest_only",
            )
        ).scalar()
        or 0
    )
    proactive_candidate_count = len(list_pending_schedule_candidates(session, user_id))

    feedback_events = session.scalars(
        select(UserFeedbackEvent)
        .where(UserFeedbackEvent.user_id == user_id)
        .order_by(UserFeedbackEvent.created_at_utc.desc())
    ).all()
    recent_feedback = [event for event in feedback_events if event.created_at_utc and event.created_at_utc >= month_ago]
    feedback_counter = Counter(event.feedback_signal for event in recent_feedback)
    profile = get_user_writing_profile(session, user_id)
    preference_vector = dict(getattr(profile, "preference_vector", None) or {}) if profile else {}

    summary_cards = [
        {
            "key": "processed_emails",
            "label": "Recent Processed Emails",
            "value": processed_email_count,
            "subtitle": "Last 7 days",
        },
        {
            "key": "pending_tags",
            "label": "Pending Category Suggestions",
            "value": len(pending_suggestions),
            "subtitle": "Awaiting accept or reject",
        },
        {
            "key": "recent_schedule",
            "label": "Recent Tentative Events",
            "value": recent_written_count,
            "subtitle": "Outlook drafts written in the last 7 days",
        },
        {
            "key": "pending_reviews",
            "label": "Pending Reply Reviews",
            "value": len(pending_review_items),
            "subtitle": "Human approval required",
        },
    ]

    return {
        "user_id": user_id,
        "summary_cards": summary_cards,
        "pending_review_items": pending_review_items,
        "pending_tag_suggestions": pending_suggestions,
        "category_distribution": category_distribution,
        "top_relationships": _top_relationships(session, user_id=user_id),
        "schedule_overview": {
            "recent_written_count": recent_written_count,
            "current_suggest_only_count": current_suggest_only_count,
            "proactive_candidate_count": proactive_candidate_count,
        },
        "feedback_overview": {
            "total_events": len(feedback_events),
            "recent_events": len(recent_feedback),
            "signal_counts": dict(feedback_counter),
            "preference_vector": preference_vector,
        },
        "last_refreshed_at_utc": now,
    }
