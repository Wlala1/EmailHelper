"""Sync Outlook calendar event responses back as UserFeedbackEvent records.

Called periodically by the background worker.  For every ScheduleCandidate
that was written to Outlook (write_status='written'), checks the current event
status via MS Graph and records the outcome as a feedback signal.

Response status mappings:
  accepted  → feedback_signal = "accepted"
  declined  → feedback_signal = "rejected"
  tentative → no new signal (still pending user decision)
  none      → no new signal (organizer view)
  cancelled → feedback_signal = "rejected"
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models import ScheduleCandidate
from repositories import create_feedback_event, get_feedback_events_for_user
from services.graph_service import GraphServiceError, graph_service
from services.writing_profile_service import update_preference_vector

logger = logging.getLogger(__name__)

# Outlook responseStatus.response values we act on.
_ACCEPTED = {"accepted"}
_REJECTED = {"declined"}
_SKIP = {"tentative", "notResponded", "organizer", "none"}


def sync_calendar_event_feedback(session_factory: sessionmaker, *, user_id: str) -> dict:
    """Check written ScheduleCandidate events and record accept/reject feedback.

    For each candidate that has an outlook_event_id, fetches the current event
    from MS Graph and creates a UserFeedbackEvent if the response has changed.
    Skips candidates that already have a feedback event recorded.

    Returns a summary dict with counts of accepted/rejected/skipped events.
    """
    session: Session = session_factory()
    try:
        # Find all written candidates for this user (up to 30 days back).
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        candidates = list(session.scalars(
            select(ScheduleCandidate)
            .where(
                ScheduleCandidate.user_id == user_id,
                ScheduleCandidate.write_status == "written",
                ScheduleCandidate.outlook_event_id.isnot(None),
                ScheduleCandidate.created_at_utc >= cutoff,
            )
        ).all())

        if not candidates:
            return {"accepted": 0, "rejected": 0, "skipped": 0, "errors": 0}

        # Fetch existing feedback events so we don't double-record.
        existing = get_feedback_events_for_user(session, user_id, target_type="schedule_candidate")
        already_recorded: set[str] = {e.target_id for e in existing}

        try:
            access_token = graph_service.ensure_access_token(session, user_id)
        except GraphServiceError as exc:
            logger.warning("calendar_feedback: cannot get access token for %s: %s", user_id, exc)
            return {"accepted": 0, "rejected": 0, "skipped": len(candidates), "errors": 1}

        counts = {"accepted": 0, "rejected": 0, "skipped": 0, "errors": 0}
        for candidate in candidates:
            if candidate.candidate_id in already_recorded:
                counts["skipped"] += 1
                continue
            try:
                event = graph_service.get_calendar_event(access_token, candidate.outlook_event_id)
            except GraphServiceError as exc:
                logger.debug("calendar_feedback: get_event failed for %s: %s", candidate.outlook_event_id, exc)
                counts["errors"] += 1
                continue

            response_status = (event.get("responseStatus") or {}).get("response", "")
            is_cancelled = bool(event.get("isCancelled"))

            if is_cancelled or response_status in _REJECTED:
                signal = "rejected"
                counts["rejected"] += 1
            elif response_status in _ACCEPTED:
                signal = "accepted"
                counts["accepted"] += 1
            else:
                counts["skipped"] += 1
                continue

            create_feedback_event(
                session,
                user_id=user_id,
                email_id=candidate.email_id,
                target_type="schedule_candidate",
                target_id=candidate.candidate_id,
                feedback_signal=signal,
                feedback_metadata={
                    "outlook_event_id": candidate.outlook_event_id,
                    "response_status": response_status,
                    "is_cancelled": is_cancelled,
                },
            )

        if counts["accepted"] + counts["rejected"] > 0:
            update_preference_vector(session, user_id)

        session.commit()
        return counts
    except Exception as exc:
        session.rollback()
        logger.error("calendar_feedback: unexpected error for %s: %s", user_id, exc)
        return {"accepted": 0, "rejected": 0, "skipped": 0, "errors": 1}
    finally:
        session.close()
