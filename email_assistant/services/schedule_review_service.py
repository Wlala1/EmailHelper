from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import ScheduleCandidate
from repositories import create_feedback_event, list_pending_schedule_candidates
from services.mailbox_actions_service import create_tentative_event


def list_schedule_candidates(session: Session, *, user_id: str) -> dict[str, Any]:
    rows = list_pending_schedule_candidates(session, user_id)
    candidates = []
    for row in rows:
        c: ScheduleCandidate = row["candidate"]
        email = row["email"]
        classifier = row["classifier"]
        candidates.append(
            {
                "candidate_id": c.candidate_id,
                "email_id": c.email_id,
                "title": c.title,
                "start_time_utc": c.start_time_utc,
                "end_time_utc": c.end_time_utc,
                "source_timezone": c.source_timezone,
                "is_all_day": c.is_all_day,
                "location": c.location,
                "confidence": c.confidence,
                "conflict_score": c.conflict_score,
                "action": c.action,
                "write_status": c.write_status,
                "outlook_event_id": c.outlook_event_id,
                "outlook_weblink": c.outlook_weblink,
                "email_subject": email.subject if email else None,
                "email_sender_name": email.sender_name if email else None,
                "email_sender_email": email.sender_email if email else None,
                "email_received_at_utc": email.received_at_utc if email else None,
                "classifier_summary": classifier.summary if classifier else None,
                "classifier_category": classifier.category if classifier else None,
                "classifier_urgency_score": classifier.urgency_score if classifier else None,
                "email_body_preview": email.body_preview if email else None,
                "email_body_content": email.body_content if email else None,
                "email_body_content_type": email.body_content_type if email else None,
            }
        )
    return {"user_id": user_id, "candidates": candidates}


def submit_schedule_review(
    session: Session,
    *,
    candidate_id: str,
    action: str,
) -> dict[str, Any]:
    candidate = session.scalars(
        select(ScheduleCandidate).where(
            ScheduleCandidate.candidate_id == candidate_id,
            ScheduleCandidate.is_current.is_(True),
        )
    ).first()
    if candidate is None:
        raise ValueError(f"schedule candidate not found: {candidate_id}")

    outlook_event_id = candidate.outlook_event_id
    outlook_weblink = candidate.outlook_weblink
    write_status = candidate.write_status

    if action == "accept":
        # Write the tentative event to Outlook now that the user confirmed it.
        candidate_dict = {
            "candidate_id": candidate.candidate_id,
            "title": candidate.title,
            "start_time_utc": candidate.start_time_utc,
            "end_time_utc": candidate.end_time_utc,
            "source_timezone": candidate.source_timezone,
            "is_all_day": candidate.is_all_day,
            "location": candidate.location,
            "attendees": candidate.attendees or [],
            "show_as": candidate.show_as,
        }
        write_status, outlook_event_id, outlook_weblink, error = create_tentative_event(
            session, user_id=candidate.user_id, candidate=candidate_dict
        )
        session.execute(
            update(ScheduleCandidate)
            .where(ScheduleCandidate.candidate_id == candidate_id)
            .values(
                write_status=write_status,
                outlook_event_id=outlook_event_id,
                outlook_weblink=outlook_weblink,
                last_write_error=error,
            )
        )

    signal_map = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}
    feedback_signal = signal_map.get(action, action)

    create_feedback_event(
        session,
        user_id=candidate.user_id,
        email_id=candidate.email_id,
        target_type="schedule_candidate",
        target_id=candidate.candidate_id,
        feedback_signal=feedback_signal,
        feedback_metadata={
            "review_action": action,
            "write_status": write_status,
        },
    )

    return {
        "candidate_id": candidate_id,
        "action": action,
        "feedback_signal": feedback_signal,
        "write_status": write_status,
        "outlook_event_id": outlook_event_id,
        "outlook_weblink": outlook_weblink,
    }
