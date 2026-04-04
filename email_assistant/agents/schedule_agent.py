from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from config import (
    DEFAULT_USER_TIMEZONE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
)
from models import ScheduleCandidate
from repositories import (
    get_current_attachment_results,
    get_current_classifier,
    get_email,
    get_relationship_snapshot,
    set_non_current_schedule,
)
from services.mailbox_actions_service import create_tentative_event

try:
    from dateutil import parser as dt_parser
except Exception:  # pragma: no cover - optional dependency
    dt_parser = None

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - optional dependency
    GraphDatabase = None

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


def _write_outlook_event(
    session: Session,
    *,
    user_id: str,
    candidate: dict[str, Any],
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    return create_tentative_event(session, user_id=user_id, candidate=candidate)


def _sync_candidates_to_neo4j(candidates: list[dict[str, Any]], user_id: str) -> dict[str, Any]:
    if not candidates:
        return {"status": "skipped", "reason": "no_candidates"}
    if GraphDatabase is None:
        return {"status": "skipped", "reason": "neo4j_driver_not_installed"}
    if not (NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD):
        return {"status": "skipped", "reason": "neo4j_credentials_missing"}

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
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
        return {"status": "written", "count": len(candidates)}
    except Exception as exc:
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

    time_expressions = list(classifier.time_expressions or [])
    for result in attachment_results:
        time_expressions.extend(result.time_expressions or [])
    time_expressions = list(dict.fromkeys(time_expressions))

    candidates: list[dict[str, Any]] = []
    for index, expr in enumerate(time_expressions):
        start_time = _parse_time_expression(expr)
        if start_time is None:
            continue
        end_time = start_time + timedelta(hours=1)
        candidate_id = str(uuid4())
        confidence = 0.8 if expr in (classifier.time_expressions or []) else 0.7
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
                "source_timezone": relationship_snapshot.get("source_timezone", DEFAULT_USER_TIMEZONE)
                if relationship_snapshot
                else DEFAULT_USER_TIMEZONE,
                "is_all_day": False,
                "location": None,
                "attendees": [],
                "confidence": round(confidence, 4),
                "conflict_score": 0.1,
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

    neo4j_sync = _sync_candidates_to_neo4j(candidates, user_id=user_id)
    return {
        "candidates": [_candidate_to_payload(item) for item in candidates],
        "neo4j_sync": neo4j_sync,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
