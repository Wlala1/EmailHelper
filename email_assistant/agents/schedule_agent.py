from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

import logging

from config import DEFAULT_USER_TIMEZONE
from models import ScheduleCandidate
from repositories import (
    get_current_attachment_results,
    get_current_classifier,
    get_email,
    get_relationship_snapshot,
    get_unaccepted_high_priority_candidates,
    get_user_writing_profile,
    set_non_current_schedule,
)
from services.mailbox_actions_service import check_free_busy, create_tentative_event
from services.neo4j_service import is_neo4j_available

logger = logging.getLogger(__name__)

try:
    from dateutil import parser as dt_parser
except Exception:  # pragma: no cover - optional dependency
    dt_parser = None

def _parse_time_expression(expression: str) -> Optional[datetime]:
    expression = expression.strip()
    if not expression:
        return None
    try:
        if expression.endswith("Z"):
            return datetime.fromisoformat(expression.replace("Z", "+00:00")).astimezone(timezone.utc)
        parsed = datetime.fromisoformat(expression)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    if dt_parser is not None:
        try:
            parsed = dt_parser.parse(expression)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _resolve_end_time(expression: str, start_time: datetime) -> datetime:
    expression = (expression or "").strip()
    if not expression:
        return start_time + timedelta(hours=1)

    lowered = expression.lower()
    for separator in (" to ", " - ", " — ", " until "):
        if separator not in lowered:
            continue
        idx = lowered.find(separator)
        right = expression[idx + len(separator):]
        candidate_end = _parse_time_expression(right)
        if candidate_end is not None and candidate_end > start_time:
            return candidate_end

    duration_match = None
    import re

    for pattern in (
        r"(\d+)\s*(minutes|minute|mins|min)\b",
        r"(\d+)\s*(hours|hour|hrs|hr)\b",
    ):
        duration_match = re.search(pattern, lowered)
        if duration_match:
            break
    if duration_match:
        value = int(duration_match.group(1))
        unit = duration_match.group(2)
        if unit.startswith(("hour", "hr")):
            return start_time + timedelta(hours=value)
        return start_time + timedelta(minutes=value)

    return start_time + timedelta(hours=1)


def _compute_conflict_score(
    candidate_start: datetime,
    candidate_end: datetime,
    free_busy_items: list[dict[str, Any]],
) -> float:
    """Compute a conflict score in [0.0, 1.0] for a candidate time slot.

    Returns:
      0.0  — no overlap with any existing calendar event
      0.5  — partial overlap
      1.0  — fully blocked (an existing event covers the entire slot)
    """
    for schedule in free_busy_items:
        for item in schedule.get("scheduleItems", []):
            item_start_raw = (item.get("start") or {}).get("dateTime")
            item_end_raw = (item.get("end") or {}).get("dateTime")
            if not item_start_raw or not item_end_raw:
                continue
            try:
                item_start = datetime.fromisoformat(item_start_raw).replace(tzinfo=timezone.utc)
                item_end = datetime.fromisoformat(item_end_raw).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            # Check overlap: [candidate_start, candidate_end) vs [item_start, item_end)
            if item_end <= candidate_start or item_start >= candidate_end:
                continue  # no overlap
            # Full containment → fully blocked
            if item_start <= candidate_start and item_end >= candidate_end:
                return 1.0
            # Partial overlap
            return 0.5
    return 0.0


def _write_outlook_event(
    session: Session,
    *,
    user_id: str,
    candidate: dict[str, Any],
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    return create_tentative_event(session, user_id=user_id, candidate=candidate)


def _sync_candidates_to_neo4j(
    candidates: list[dict[str, Any]],
    user_id: str,
    sender_email: Optional[str] = None,
) -> dict[str, Any]:
    if not candidates:
        return {"status": "skipped", "reason": "no_candidates"}
    if not is_neo4j_available():
        return {"status": "skipped", "reason": "neo4j_not_available"}

    from services.neo4j_service import get_neo4j_driver

    driver = get_neo4j_driver()
    try:
        with driver.session() as neo_session:
            for cand in candidates:
                neo_session.run(
                    """
                    MERGE (u:User {user_id: $user_id})
                    MERGE (e:EventCandidate {candidate_id: $candidate_id})
                    SET e.title = $title,
                        e.start_time_utc = $start_time_utc,
                        e.end_time_utc = $end_time_utc,
                        e.source = $source,
                        e.confidence = $confidence,
                        e.action = $action
                    MERGE (u)-[:HAS_EVENT_CANDIDATE]->(e)
                    """,
                    user_id=user_id,
                    candidate_id=cand["candidate_id"],
                    title=cand["title"],
                    start_time_utc=cand["start_time_utc"].isoformat(),
                    end_time_utc=cand["end_time_utc"].isoformat(),
                    source=cand["source"],
                    confidence=cand["confidence"],
                    action=cand["action"],
                )
                # Link EventCandidate to the sender Person node.
                if sender_email:
                    neo_session.run(
                        """
                        MATCH (e:EventCandidate {candidate_id: $candidate_id})
                        MATCH (p:Person {email: $sender_email})
                        MERGE (e)-[:INVOLVES]->(p)
                        """,
                        candidate_id=cand["candidate_id"],
                        sender_email=sender_email,
                    )
        return {"status": "written", "count": len(candidates)}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("_sync_candidates_to_neo4j failed: %s", exc)
        return {"status": "failed", "error": str(exc)}
    finally:
        driver.close()


def _candidate_to_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload["start_time_utc"] = item["start_time_utc"].isoformat()
    payload["end_time_utc"] = item["end_time_utc"].isoformat()
    return payload


def run_schedule(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, Any]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")

    classifier = get_current_classifier(session, email_id)
    if classifier is None:
        raise ValueError("classifier current result missing")

    attachment_results = get_current_attachment_results(session, email_id)
    relationship_snapshot = get_relationship_snapshot(session, email_id)

    # Query Neo4j for the sender's graph context: shared past events and
    # observation depth.  Used to boost confidence on candidates from recurring
    # or graph-connected senders.
    neo4j_context: dict[str, Any] = {}
    try:
        from services.neo4j_service import get_person_context
        ctx = get_person_context(user_id=user_id, person_email=email.sender_email)
        if ctx:
            neo4j_context = ctx
    except Exception as exc:
        logger.debug("schedule: Neo4j person context unavailable: %s", exc)

    sender_shared_events: list[str] = neo4j_context.get("shared_events") or []
    neo4j_observation_count: int = int(neo4j_context.get("observation_count") or 0)
    shared_events_count = len(sender_shared_events)
    # Confidence boost for senders with a rich relationship history in the graph.
    graph_confidence_boost = min(0.15, neo4j_observation_count * 0.01) + min(0.15, shared_events_count * 0.03)

    time_expressions = list(classifier.time_expressions or [])
    for result in attachment_results:
        time_expressions.extend(result.time_expressions or [])
    time_expressions = list(dict.fromkeys(time_expressions))

    candidates: list[dict[str, Any]] = []
    for index, expr in enumerate(time_expressions):
        start_time = _parse_time_expression(expr)
        if start_time is None:
            continue
        end_time = _resolve_end_time(expr, start_time)
        candidate_id = str(uuid4())
        base_confidence = 0.8 if expr in (classifier.time_expressions or []) else 0.7
        # Boost confidence if sender has shared events or deep observation history.
        confidence = min(0.98, base_confidence + graph_confidence_boost)
        action = "create_tentative_event" if confidence >= 0.75 else "suggest_only"
        source = "email" if expr in (classifier.time_expressions or []) else "attachment"
        if expr in (classifier.time_expressions or []) and any(expr in (r.time_expressions or []) for r in attachment_results):
            source = "both"

        candidates.append(
            {
                "candidate_id": candidate_id,
                "source": source,
                "title": classifier.summary[:80] or (email.subject or "OUMA Calendar Event"),
                "start_time_utc": start_time,
                "end_time_utc": end_time,
                "source_timezone": (relationship_snapshot.get("source_timezone") if relationship_snapshot else None) or DEFAULT_USER_TIMEZONE,
                "is_all_day": False,
                "location": None,
                "attendees": [],
                "confidence": round(confidence, 4),
                "conflict_score": 0.1,
                "graph_boost": round(graph_confidence_boost, 4),
                "shared_events_count": shared_events_count,
                "calendar_conflict_penalty": 0.0,
                "preference_adjustment": 0.0,
                "recommendation_rank": index + 1,
                "action": action,
                "show_as": "tentative",
                "transaction_id": f"ouma_sched_{candidate_id}",
                "outlook_event_id": None,
                "outlook_weblink": None,
                "write_status": "pending",
                "last_write_error": None,
            }
        )

    if not candidates:
        candidate_id = str(uuid4())
        start_time = email.received_at_utc.astimezone(timezone.utc) + timedelta(days=1)
        candidates.append(
            {
                "candidate_id": candidate_id,
                "source": "email",
                "title": email.subject or "Follow-up reminder",
                "start_time_utc": start_time,
                "end_time_utc": start_time + timedelta(hours=1),
                "source_timezone": DEFAULT_USER_TIMEZONE,
                "is_all_day": False,
                "location": None,
                "attendees": [],
                "confidence": 0.35,
                "conflict_score": 0.2,
                "graph_boost": round(graph_confidence_boost, 4),
                "shared_events_count": shared_events_count,
                "calendar_conflict_penalty": 0.0,
                "preference_adjustment": 0.0,
                "recommendation_rank": 1,
                "action": "suggest_only",
                "show_as": "tentative",
                "transaction_id": f"ouma_sched_{candidate_id}",
                "outlook_event_id": None,
                "outlook_weblink": None,
                "write_status": "pending",
                "last_write_error": None,
            }
        )

    # --- Phase C: Free/busy lookup and conflict detection ---
    # Fetch the calendar window covering all candidates + 30-min buffer.
    if candidates:
        window_start = min(c["start_time_utc"] for c in candidates) - timedelta(minutes=30)
        window_end = max(c["end_time_utc"] for c in candidates) + timedelta(minutes=30)
        try:
            free_busy_items = check_free_busy(
                session,
                user_id=user_id,
                start_time_utc=window_start,
                end_time_utc=window_end,
            )
        except Exception as exc:
            logger.warning("Free/busy lookup failed, defaulting conflict_score to 0.1: %s", exc)
            free_busy_items = []

        for item in candidates:
            if free_busy_items:
                item["conflict_score"] = _compute_conflict_score(
                    item["start_time_utc"], item["end_time_utc"], free_busy_items
                )
            item["calendar_conflict_penalty"] = round(item["conflict_score"] * 3.0, 4)
            # Downgrade to suggest_only if heavily conflicted.
            if item["conflict_score"] >= 0.8 and item["action"] == "create_tentative_event":
                item["action"] = "suggest_only"

    # --- Phase C+E: Relationship-weighted + feedback-adjusted ranking ---
    relationship_weight = float((relationship_snapshot or {}).get("relationship_weight", 0.5))
    # Load preference vector for feedback-driven ranking adjustment (Phase E6).
    from config import USE_PREFERENCE_VECTOR
    schedule_accept_rate = 0.5
    if USE_PREFERENCE_VECTOR:
        profile = get_user_writing_profile(session, user_id)
        pref_vector = dict(getattr(profile, "preference_vector", None) or {}) if profile else {}
        schedule_accept_rate = float(pref_vector.get("schedule_accept_rate", 0.5))

    for i, item in enumerate(candidates):
        shared_event_boost = min(1.5, item["shared_events_count"] * 0.35)
        preference_adjustment = round(schedule_accept_rate * 1.5, 4)
        rank_score = (
            (i + 1)
            - (relationship_weight * 2.0)
            - shared_event_boost
            + item["calendar_conflict_penalty"]
            - preference_adjustment
        )
        # High acceptance rate → user likes schedule suggestions → boost rank (lower score = higher priority).
        item["_rank_score"] = rank_score
        item["preference_adjustment"] = preference_adjustment
    candidates.sort(key=lambda x: x["_rank_score"])
    for i, item in enumerate(candidates):
        item["recommendation_rank"] = i + 1
        item.pop("_rank_score", None)

    for item in candidates:
        if item["action"] == "create_tentative_event":
            write_status, event_id, web_link, error = _write_outlook_event(
                session,
                user_id=user_id,
                candidate=item,
            )
            item["write_status"] = write_status
            item["outlook_event_id"] = event_id
            item["outlook_weblink"] = web_link
            item["last_write_error"] = error
        else:
            item["write_status"] = "pending"

    set_non_current_schedule(session, email_id)
    for item in candidates:
        session.add(
            ScheduleCandidate(
                run_id=run_id,
                trace_id=trace_id,
                email_id=email_id,
                user_id=user_id,
                candidate_id=item["candidate_id"],
                source=item["source"],
                title=item["title"],
                start_time_utc=item["start_time_utc"],
                end_time_utc=item["end_time_utc"],
                source_timezone=item["source_timezone"],
                is_all_day=item["is_all_day"],
                location=item["location"],
                attendees=item["attendees"],
                confidence=item["confidence"],
                conflict_score=item["conflict_score"],
                recommendation_rank=item["recommendation_rank"],
                action=item["action"],
                show_as=item["show_as"],
                transaction_id=item["transaction_id"],
                outlook_event_id=item["outlook_event_id"],
                outlook_weblink=item["outlook_weblink"],
                write_status=item["write_status"],
                last_write_error=item["last_write_error"],
                is_current=True,
            )
        )

    neo4j_sync = _sync_candidates_to_neo4j(candidates, user_id=user_id, sender_email=email.sender_email)

    # --- Phase C: Proactive recommendations (past unaccepted high-priority events) ---
    proactive = get_unaccepted_high_priority_candidates(session, user_id)
    proactive_payload = [
        {
            "candidate_id": c.candidate_id,
            "title": c.title,
            "start_time_utc": c.start_time_utc.isoformat(),
            "action": c.action,
            "confidence": c.confidence,
            "email_id": c.email_id,
        }
        for c in proactive
        if c.email_id != email_id  # exclude candidates from the current email
    ]

    return {
        "candidates": [_candidate_to_payload(item) for item in candidates],
        "proactive_suggestions": proactive_payload,
        "sender_shared_events": sender_shared_events,
        "neo4j_sync": neo4j_sync,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
