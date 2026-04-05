from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agents.classification_agent import run_classifier
from agents.intake_agent import run_intake
from agents.relationship_graph_agent import run_relationship_graph
from agents.response_agent import run_response
from agents.schedule_agent import run_schedule
from config import AUTO_DRAFT_RELATIONSHIP_THRESHOLD
from models import Email, EmailRecipient, RelationshipObservation
from repositories import (
    create_agent_run,
    create_reply_draft_write,
    finalize_agent_run_failed,
    finalize_agent_run_success,
    get_current_classifier,
    get_current_reply_suggestion,
    get_current_top_schedule_candidate,
    get_email,
    get_latest_branch_statuses,
    get_latest_reply_draft_write,
    get_relationship_snapshot,
    get_user_writing_profile,
)
from schemas import AgentRunStatus, MailboxFolder, ProcessedMode
from services.mailbox_actions_service import create_reply_draft
from services.graph_service import attachment_to_payload, graph_service, message_body_to_html, parse_graph_datetime
from services.writing_profile_service import rebuild_user_writing_profile

AgentRunner = Callable[..., dict[str, Any]]

MEETING_CATEGORY_RE = re.compile(
    r"\b(meetings?|schedule|calendar|call|interview|sync|appointment)\b",
    re.IGNORECASE,
)
BULK_SENDER_RE = re.compile(r"(^|[^a-z])(no-?reply|newsletter|notifications?)([^a-z]|$)", re.IGNORECASE)


def _stable_email_id(raw_value: str) -> str:
    if len(raw_value) <= 64:
        return raw_value
    digest = hashlib.sha1(raw_value.encode("utf-8")).hexdigest()
    return f"g_{digest[:40]}"


def _execute_agent_step(
    session_factory: sessionmaker,
    *,
    agent_name: str,
    trace_id: str,
    email_id: str,
    user_id: str,
    input_payload: dict[str, Any],
    runner: AgentRunner,
    error_code: str,
    runner_kwargs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = str(uuid4())
    session: Session = session_factory()
    try:
        create_agent_run(
            session,
            run_id=run_id,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            agent_name=agent_name,
            input_payload=input_payload,
        )
        output = runner(
            session,
            trace_id=trace_id,
            run_id=run_id,
            email_id=email_id,
            user_id=user_id,
            **(runner_kwargs or {}),
        )
        session.flush()
        finalize_agent_run_success(session, run_id, output)
        session.commit()
        return output
    except Exception as exc:
        session.rollback()
        try:
            finalize_agent_run_failed(session, run_id, error_code, str(exc))
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()


def execute_intake(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _execute_agent_step(
        session_factory,
        agent_name="intake",
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        input_payload=payload,
        runner=run_intake,
        error_code="INTAKE_ERROR",
        runner_kwargs={"payload": payload},
    )


def execute_classifier(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
    category_catalog_override: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    return _execute_agent_step(
        session_factory,
        agent_name="classifier",
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        input_payload={
            "email_id": email_id,
            "category_catalog_override": category_catalog_override or [],
        },
        runner=run_classifier,
        error_code="CLASSIFIER_ERROR",
        runner_kwargs={"category_catalog_override": category_catalog_override},
    )


def execute_relationship_graph(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, Any]:
    return _execute_agent_step(
        session_factory,
        agent_name="relationship_graph",
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        input_payload={"email_id": email_id},
        runner=run_relationship_graph,
        error_code="RELATIONSHIP_GRAPH_ERROR",
    )


def execute_schedule(session_factory: sessionmaker, *, trace_id: str, email_id: str, user_id: str) -> dict[str, Any]:
    return _execute_agent_step(
        session_factory,
        agent_name="schedule",
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        input_payload={"email_id": email_id},
        runner=run_schedule,
        error_code="SCHEDULE_ERROR",
    )


def execute_response(session_factory: sessionmaker, *, trace_id: str, email_id: str, user_id: str) -> dict[str, Any]:
    with session_factory() as session:
        statuses = get_latest_branch_statuses(session, trace_id, email_id, ["attachment", "relationship_graph", "schedule"])
    for name, status in statuses.items():
        if status not in {AgentRunStatus.success.value, AgentRunStatus.skipped.value}:
            raise RuntimeError(f"response blocked: branch '{name}' status is '{status}'")
    return _execute_agent_step(
        session_factory,
        agent_name="response",
        trace_id=trace_id,
        email_id=email_id,
        user_id=user_id,
        input_payload={"email_id": email_id},
        runner=run_response,
        error_code="RESPONSE_ERROR",
        runner_kwargs={"attachment_status": statuses.get("attachment") or AgentRunStatus.skipped.value},
    )


def build_graph_intake_payload(
    *,
    user_id: str,
    primary_email: Optional[str],
    display_name: Optional[str],
    timezone_name: Optional[str],
    message: dict[str, Any],
    folder: str,
    processed_mode: str,
    attachments: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    body_content_type, body_content = message_body_to_html(message.get("body") or {})
    sender = (message.get("from") or {}).get("emailAddress") or {}
    received_at = parse_graph_datetime(message.get("receivedDateTime") or message.get("sentDateTime")) or datetime.now(timezone.utc)
    raw_message_id = message.get("id") or message.get("internetMessageId") or str(uuid4())
    email_id = _stable_email_id(raw_message_id)
    mailbox_folder = {
        "inbox": MailboxFolder.inbox,
        "sentitems": MailboxFolder.sent,
    }.get(folder.lower(), MailboxFolder.other)
    processed = ProcessedMode.live if processed_mode == "live" else ProcessedMode.bootstrap
    direction = "outbound" if mailbox_folder == MailboxFolder.sent else "inbound"
    last_modified_at = parse_graph_datetime(message.get("lastModifiedDateTime"))

    recipients: list[dict[str, Any]] = []
    for recipient_type, key in (("to", "toRecipients"), ("cc", "ccRecipients")):
        for item in message.get(key, []) or []:
            address = (item.get("emailAddress") or {})
            email = address.get("address")
            if not email:
                continue
            recipients.append(
                {
                    "recipient_email": email,
                    "recipient_name": address.get("name"),
                    "recipient_type": recipient_type,
                }
            )

    from utils.pii import anonymize_text

    payload = {
        "user": {
            "user_id": user_id,
            "primary_email": primary_email,
            "display_name": display_name,
            "timezone": timezone_name,
        },
        "email": {
            "email_id": email_id,
            "graph_message_id": message.get("id"),
            "graph_immutable_id": message.get("id"),
            "internet_message_id": message.get("internetMessageId"),
            "conversation_id": message.get("conversationId"),
            "graph_parent_folder_id": message.get("parentFolderId"),
            "sender_name": sender.get("name"),
            "sender_email": sender.get("address") or primary_email or "unknown@example.com",
            "subject": anonymize_text(message.get("subject") or ""),
            "body_content_type": body_content_type,
            "body_content": anonymize_text(body_content or ""),
            "body_preview": anonymize_text(message.get("bodyPreview") or ""),
            "received_at_utc": received_at.isoformat(),
            "has_attachments": bool(message.get("hasAttachments")),
            "direction": direction,
            "mailbox_folder": mailbox_folder.value,
            "mailbox_last_modified_at_utc": last_modified_at.isoformat() if last_modified_at else None,
            "processed_mode": processed.value,
        },
        "email_recipients": recipients,
        "attachments": [attachment_to_payload(item) for item in attachments],
    }
    return email_id, payload


def learn_from_outbound_email(session_factory: sessionmaker, *, email_id: str, user_id: str) -> dict[str, Any]:
    session: Session = session_factory()
    try:
        email = get_email(session, email_id)
        if email is None:
            raise ValueError(f"email_id not found: {email_id}")
        recipients = session.scalars(select(EmailRecipient).where(EmailRecipient.email_id == email_id)).all()
        observations = 0
        for recipient in recipients:
            session.add(
                RelationshipObservation(
                    run_id=str(uuid4()),
                    trace_id=str(uuid4()),
                    email_id=email_id,
                    user_id=user_id,
                    person_email=recipient.recipient_email,
                    person_name=recipient.recipient_name,
                    person_role="Recipient",
                    organisation_name=None,
                    organisation_domain=(recipient.recipient_email.split("@", 1)[1].lower() if "@" in recipient.recipient_email else None),
                    signal_type=f"email_outbound_{recipient.recipient_type}",
                    signal_weight=0.6,
                    observed_at_utc=email.received_at_utc,
                )
            )
            observations += 1
        session.flush()
        profile = rebuild_user_writing_profile(session, user_id)
        session.commit()
        return {"observations_added": observations, "profile": profile}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _preferred_tone_key(profile: Optional[dict[str, Any]]) -> str:
    """Return the preferred tone key for this user.

    When USE_PREFERENCE_VECTOR=true, picks the tone with the highest acceptance
    rate from the preference_vector.  Falls back to the style-profile mapping.
    """
    from config import USE_PREFERENCE_VECTOR
    if USE_PREFERENCE_VECTOR:
        pref_vector = (profile or {}).get("preference_vector") or {}
        tone_accept_rates: dict[str, float] = pref_vector.get("tone_accept_rates", {})
        if tone_accept_rates:
            return max(tone_accept_rates, key=lambda k: tone_accept_rates[k])
    # Fallback: map style profile to tone key.
    tone_profile = (profile or {}).get("tone_profile")
    if tone_profile == "formal":
        return "professional"
    if tone_profile == "warm":
        return "casual"
    return "colloquial"


def _should_auto_create_draft(
    *,
    email: Email,
    classifier: Optional[Any],
    relationship_snapshot: Optional[dict[str, Any]],
    top_candidate: Optional[Any],
    reply_required: bool,
) -> tuple[bool, str]:
    if not reply_required:
        return False, "reply_not_required"
    if BULK_SENDER_RE.search(email.sender_email or ""):
        return False, "bulk_sender"

    category = getattr(classifier, "category", "") or ""
    meeting_signal = bool(MEETING_CATEGORY_RE.search(category)) or getattr(top_candidate, "action", None) == "create_tentative_event"
    high_relationship = float((relationship_snapshot or {}).get("relationship_weight", 0.0)) >= AUTO_DRAFT_RELATIONSHIP_THRESHOLD
    if meeting_signal:
        return True, "meeting_or_schedule_signal"
    if high_relationship:
        return True, "high_relationship_signal"
    return False, "policy_not_matched"


def maybe_create_reply_draft(session_factory: sessionmaker, *, user_id: str, email_id: str) -> dict[str, Any]:
    session: Session = session_factory()
    try:
        reply = get_current_reply_suggestion(session, email_id)
        latest_write = get_latest_reply_draft_write(session, email_id)
        if reply is None:
            raise ValueError(f"reply suggestion missing for email {email_id}")
        if latest_write and latest_write.reply_suggestion_id == reply.id and latest_write.draft_status in {
            "pending_review",
            "written",
            "rejected",
            "deferred",
            "skipped",
        }:
            return {
                "draft_status": latest_write.draft_status,
                "policy_name": latest_write.policy_name,
                "outlook_draft_id": latest_write.outlook_draft_id,
                "outlook_web_link": latest_write.outlook_web_link,
                "error_message": latest_write.error_message,
            }

        if not reply.reply_required:
            record = create_reply_draft_write(
                session,
                reply_suggestion_id=reply.id,
                user_id=user_id,
                email_id=email_id,
                policy_name="reply_not_required",
                draft_status="skipped",
            )
            session.commit()
            return {"draft_status": record.draft_status, "policy_name": record.policy_name}

        record = create_reply_draft_write(
            session,
            reply_suggestion_id=reply.id,
            user_id=user_id,
            email_id=email_id,
            policy_name="pending_human_review",
            draft_status="pending_review",
        )
        session.commit()
        return {
            "draft_status": record.draft_status,
            "policy_name": record.policy_name,
            "outlook_draft_id": record.outlook_draft_id,
            "outlook_web_link": record.outlook_web_link,
        }
    except Exception as exc:
        session.rollback()
        try:
            reply = get_current_reply_suggestion(session, email_id)
            create_reply_draft_write(
                session,
                reply_suggestion_id=reply.id if reply else None,
                user_id=user_id,
                email_id=email_id,
                policy_name="draft_writeback",
                draft_status="failed",
                error_message=str(exc),
            )
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()


def process_live_inbox_email(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, Any]:
    classifier_output = execute_classifier(session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
    relationship_output = execute_relationship_graph(session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
    schedule_output = execute_schedule(session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
    response_output = execute_response(session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
    draft_output = maybe_create_reply_draft(session_factory, user_id=user_id, email_id=email_id)
    response_output["draft_write"] = draft_output
    return {
        "classifier": classifier_output,
        "relationship_graph": relationship_output,
        "schedule": schedule_output,
        "response": response_output,
        "draft_write": draft_output,
    }


def process_historical_inbox_email(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, Any]:
    classifier_output = execute_classifier(session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
    relationship_output = execute_relationship_graph(session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
    return {
        "classifier": classifier_output,
        "relationship_graph": relationship_output,
    }
