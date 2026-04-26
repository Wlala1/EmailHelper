from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy.orm import Session

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
except Exception:
    dt_parser = None


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

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
    import re
    lowered = expression.lower()
    for separator in (" to ", " - ", " — ", " until "):
        if separator not in lowered:
            continue
        idx = lowered.find(separator)
        right = expression[idx + len(separator):]
        candidate_end = _parse_time_expression(right)
        if candidate_end is not None and candidate_end > start_time:
            return candidate_end
    for pattern in (
        r"(\d+)\s*(minutes|minute|mins|min)\b",
        r"(\d+)\s*(hours|hour|hrs|hr)\b",
    ):
        m = re.search(pattern, lowered)
        if m:
            value = int(m.group(1))
            unit = m.group(2)
            if unit.startswith(("hour", "hr")):
                return start_time + timedelta(hours=value)
            return start_time + timedelta(minutes=value)
    return start_time + timedelta(hours=1)


def _compute_conflict_score(
    candidate_start: datetime,
    candidate_end: datetime,
    free_busy_items: list[dict[str, Any]],
) -> float:
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
            if item_end <= candidate_start or item_start >= candidate_end:
                continue
            if item_start <= candidate_start and item_end >= candidate_end:
                return 1.0
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
        logger.warning("_sync_candidates_to_neo4j failed: %s", exc)
        return {"status": "failed", "error": str(exc)}
    finally:
        driver.close()


def _candidate_to_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload["start_time_utc"] = item["start_time_utc"].isoformat()
    payload["end_time_utc"] = item["end_time_utc"].isoformat()
    return payload


# ---------------------------------------------------------------------------
# Direct tool implementations (plain functions, no @tool decorator)
# ---------------------------------------------------------------------------

def _parse_natural_time_fn(text: str, ref_date_str: str) -> dict:
    """Parse a time expression to {start_iso, end_iso, confidence} or {error}."""
    parsed = _parse_time_expression(text)
    if parsed is not None:
        end = _resolve_end_time(text, parsed)
        return {"start_iso": parsed.isoformat(), "end_iso": end.isoformat(), "confidence": 0.9}
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[{
                "role": "user",
                "content": (
                    f"Reference date/time: {ref_date_str}.\n"
                    f"Convert this time expression to ISO 8601 UTC: \"{text}\".\n"
                    "Reply ONLY with JSON: {\"start\": \"<ISO>\", \"end\": \"<ISO>\"}.\n"
                    "Assume 1-hour duration if no end time is specified."
                ),
            }],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return {"start_iso": data["start"], "end_iso": data["end"], "confidence": 0.75}
    except Exception as exc:
        logger.debug("parse_natural_time LLM fallback failed: %s", exc)
        return {"error": f"could not parse: {text}"}


def _check_calendar_slot_fn(
    start_iso: str,
    end_iso: str,
    session: Session,
    user_id: str,
) -> dict:
    """Check calendar availability. Returns {status, conflict_count}."""
    try:
        start = datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc)
        items = check_free_busy(session, user_id=user_id, start_time_utc=start, end_time_utc=end)
        conflict_count = sum(len(s.get("scheduleItems", [])) for s in items)
        if conflict_count == 0:
            status = "free"
        else:
            score = _compute_conflict_score(start, end, items)
            status = "full_conflict" if score >= 1.0 else "partial_conflict"
        return {"status": status, "conflict_count": conflict_count}
    except Exception as exc:
        logger.warning("check_calendar_slot error: %s", exc)
        return {"status": "unknown", "error": str(exc)}


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM output
# ---------------------------------------------------------------------------

class ScheduleIntentOutput(BaseModel):
    has_intent: bool
    reason: str


class CandidateProposal(BaseModel):
    title: str
    start_iso: str
    end_iso: str
    confidence: float
    action: str          # "create_tentative_event" | "suggest_only"
    location: Optional[str] = None
    attendees: list[str] = []


class ProposalOutput(BaseModel):
    candidates: list[CandidateProposal]


# ---------------------------------------------------------------------------
# Module-level LLM chains (singletons — not rebuilt per email)
# ---------------------------------------------------------------------------

try:
    _intent_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    _intent_chain = _intent_llm.with_structured_output(ScheduleIntentOutput)
    _proposal_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    _proposal_chain = _proposal_llm.with_structured_output(ProposalOutput)
except Exception:
    _intent_chain = None
    _proposal_chain = None

_INTENT_PROMPT = """You are a scheduling assistant. Decide whether the email contains explicit scheduling intent
(i.e. it proposes or requests a specific meeting, event, appointment, or deadline that should be added to a calendar).

Signals of scheduling intent:
- Specific dates/times mentioned ("Monday 3pm", "next Friday", "2026-05-01")
- Words like "meeting", "call", "seminar", "workshop", "deadline", "appointment"
- Invitations or requests to attend an event

Signals of NO scheduling intent:
- General announcements or newsletters
- Informational updates with no action required
- Automated notifications with no specific proposed time

Reply with has_intent=true only if there is a concrete, actionable scheduling signal."""

_PROPOSAL_PROMPT = """You are a scheduling assistant. Given an email and a list of parsed time slots with
calendar availability, produce a ranked list of schedule candidates.

Rules:
- action = "create_tentative_event" if confidence >= 0.75 AND slot_status = "free"
- action = "suggest_only" in all other cases
- Extract title from the email subject or body (≤ 80 chars)
- Raise confidence for high relationship_weight senders; lower for vague time hints
- If none of the slots are suitable, return an empty candidates list"""


# ---------------------------------------------------------------------------
# Two-step pipeline (replaces ReAct agent)
# ---------------------------------------------------------------------------

def _run_schedule_pipeline(
    session: Session,
    *,
    user_id: str,
    email_subject: str,
    email_body: str,
    sender_email: str,
    received_at: datetime,
    time_hints: list[str],
    classifier_summary: str,
    relationship_weight: float,
) -> list[dict]:
    """
    Step 1: one structured LLM call to detect scheduling intent.
    Step 2 (if intent): call parse/check functions directly, then one LLM call to propose candidates.
    Returns raw candidate dicts compatible with the downstream run_schedule() conversion loop.
    """
    # ── Step 1: Intent check ────────────────────────────────────────────────
    intent_payload = json.dumps({
        "subject": email_subject,
        "classifier_summary": classifier_summary,
        "time_hints": time_hints,
        "relationship_weight": relationship_weight,
        "body_preview": email_body[:500],
    }, ensure_ascii=False)

    has_intent = False
    if _intent_chain is not None:
        try:
            intent_result = _intent_chain.invoke([
                SystemMessage(content=_INTENT_PROMPT),
                HumanMessage(content=intent_payload),
            ])
            has_intent = intent_result.has_intent
            logger.debug("Schedule intent for %s: %s — %s", email_subject[:40], has_intent, intent_result.reason)
        except Exception as exc:
            logger.warning("Intent check failed, using heuristic: %s", exc)
            has_intent = len(time_hints) > 0
    else:
        has_intent = len(time_hints) > 0

    if not has_intent:
        return []

    # ── Step 2: Parse hints + check calendar directly ───────────────────────
    ref_date_str = received_at.strftime("%Y-%m-%d %H:%M UTC")
    parsed_slots: list[dict] = []

    for hint in time_hints:
        slot = _parse_natural_time_fn(hint, ref_date_str)
        if "error" in slot:
            continue
        availability = _check_calendar_slot_fn(slot["start_iso"], slot["end_iso"], session, user_id)
        parsed_slots.append({
            "hint": hint,
            "start_iso": slot["start_iso"],
            "end_iso": slot["end_iso"],
            "parse_confidence": slot["confidence"],
            "slot_status": availability.get("status", "unknown"),
            "conflict_count": availability.get("conflict_count", 0),
        })

    # ── Step 3: Proposal ─────────────────────────────────────────────────────
    if _proposal_chain is None or not parsed_slots:
        return []

    proposal_payload = json.dumps({
        "subject": email_subject,
        "body_preview": email_body[:1500],
        "classifier_summary": classifier_summary,
        "sender_email": sender_email,
        "relationship_weight": relationship_weight,
        "parsed_slots": parsed_slots,
    }, ensure_ascii=False)

    try:
        proposal = _proposal_chain.invoke([
            SystemMessage(content=_PROPOSAL_PROMPT),
            HumanMessage(content=proposal_payload),
        ])
        return [c.model_dump() for c in proposal.candidates]
    except Exception as exc:
        logger.warning("Proposal LLM failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

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

    time_hints = list(classifier.time_expressions or [])
    for result in attachment_results:
        time_hints.extend(result.time_expressions or [])
    time_hints = list(dict.fromkeys(time_hints))

    relationship_weight = float((relationship_snapshot or {}).get("relationship_weight", 0.5))

    raw_candidates = _run_schedule_pipeline(
        session,
        user_id=user_id,
        email_subject=email.subject or "",
        email_body=email.body_preview or email.body_content or "",
        sender_email=email.sender_email or "",
        received_at=email.received_at_utc.astimezone(timezone.utc),
        time_hints=time_hints,
        classifier_summary=classifier.summary or "",
        relationship_weight=relationship_weight,
    )

    # --- Convert agent output to full ScheduleCandidate format ---
    candidates: list[dict[str, Any]] = []
    source_timezone = (
        (relationship_snapshot.get("source_timezone") if relationship_snapshot else None)
        or DEFAULT_USER_TIMEZONE
    )

    for index, raw in enumerate(raw_candidates):
        candidate_id = str(uuid4())
        try:
            start_time = datetime.fromisoformat(raw["start_iso"]).replace(tzinfo=timezone.utc)
            end_time = datetime.fromisoformat(raw["end_iso"]).replace(tzinfo=timezone.utc)
        except Exception:
            logger.warning("Skipping candidate with invalid datetimes: %s", raw)
            continue

        confidence = float(raw.get("confidence", 0.7))
        action = raw.get("action", "suggest_only")
        if confidence < 0.75 and action == "create_tentative_event":
            action = "suggest_only"

        candidates.append({
            "candidate_id": candidate_id,
            "source": "email",
            "title": (raw.get("title") or classifier.summary[:80] or email.subject or "OUMA Calendar Event"),
            "start_time_utc": start_time,
            "end_time_utc": end_time,
            "source_timezone": source_timezone,
            "is_all_day": False,
            "location": raw.get("location"),
            "attendees": raw.get("attendees") or [],
            "confidence": round(confidence, 4),
            "conflict_score": 0.0 if action == "create_tentative_event" else 0.1,
            "graph_boost": 0.0,
            "shared_events_count": 0,
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
        })

    # Fallback: low-confidence follow-up reminder if pipeline found nothing
    if not candidates:
        candidate_id = str(uuid4())
        start_time = email.received_at_utc.astimezone(timezone.utc) + timedelta(days=1)
        candidates.append({
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
            "graph_boost": 0.0,
            "shared_events_count": 0,
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
        })

    # --- Preference-vector ranking ---
    from config import USE_PREFERENCE_VECTOR
    schedule_accept_rate = 0.5
    if USE_PREFERENCE_VECTOR:
        profile = get_user_writing_profile(session, user_id)
        pref_vector = dict(getattr(profile, "preference_vector", None) or {}) if profile else {}
        schedule_accept_rate = float(pref_vector.get("schedule_accept_rate", 0.5))

    for i, item in enumerate(candidates):
        preference_adjustment = round(schedule_accept_rate * 1.5, 4)
        rank_score = (
            (i + 1)
            - (relationship_weight * 2.0)
            + item["calendar_conflict_penalty"]
            - preference_adjustment
        )
        item["_rank_score"] = rank_score
        item["preference_adjustment"] = preference_adjustment
    candidates.sort(key=lambda x: x["_rank_score"])
    for i, item in enumerate(candidates):
        item["recommendation_rank"] = i + 1
        item.pop("_rank_score", None)

    # --- Write accepted candidates to Outlook ---
    for item in candidates:
        if item["action"] == "create_tentative_event":
            write_status, event_id, web_link, error = _write_outlook_event(
                session, user_id=user_id, candidate=item
            )
            item["write_status"] = write_status
            item["outlook_event_id"] = event_id
            item["outlook_weblink"] = web_link
            item["last_write_error"] = error

    # --- Persist to PostgreSQL ---
    set_non_current_schedule(session, email_id)
    for item in candidates:
        session.add(ScheduleCandidate(
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
            is_current=(item["recommendation_rank"] == 1),
        ))

    neo4j_sync = _sync_candidates_to_neo4j(candidates, user_id=user_id, sender_email=email.sender_email)

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
        if c.email_id != email_id
    ]

    return {
        "candidates": [_candidate_to_payload(item) for item in candidates],
        "proactive_suggestions": proactive_payload,
        "neo4j_sync": neo4j_sync,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
