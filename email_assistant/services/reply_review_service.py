from __future__ import annotations

import html
from typing import Any, Optional

from sqlalchemy.orm import Session

from repositories import (
    create_feedback_event,
    create_reply_draft_write,
    get_current_reply_suggestion,
    get_email,
    get_latest_reply_draft_write,
    get_user_writing_profile,
)
from schemas import ReplyReviewAction, ReplyReviewRequest
from services.mailbox_actions_service import create_reply_draft
from services.writing_profile_service import update_preference_vector

TERMINAL_REVIEW_STATUSES = {"written", "rejected", "deferred"}


def _preferred_tone_key(profile: Optional[Any], tone_templates: dict[str, str]) -> str:
    preference_vector = dict(getattr(profile, "preference_vector", None) or {}) if profile else {}
    tone_accept_rates: dict[str, float] = preference_vector.get("tone_accept_rates", {})
    allowed = [key for key, value in tone_templates.items() if value]
    if not allowed:
        return "professional"
    if tone_accept_rates:
        return max(allowed, key=lambda key: tone_accept_rates.get(key, 0.0))
    tone_profile = getattr(profile, "tone_profile", None) if profile else None
    if tone_profile == "warm" and "casual" in tone_templates:
        return "casual"
    if tone_profile == "casual" and "colloquial" in tone_templates:
        return "colloquial"
    if "professional" in tone_templates:
        return "professional"
    return allowed[0]


def _serialize_draft_write(write: Any | None) -> dict[str, Any] | None:
    if write is None:
        return None
    return {
        "reply_suggestion_id": write.reply_suggestion_id,
        "draft_status": write.draft_status,
        "policy_name": write.policy_name,
        "outlook_draft_id": write.outlook_draft_id,
        "outlook_web_link": write.outlook_web_link,
        "error_message": write.error_message,
    }


def _render_body_html(text: str) -> str:
    escaped = html.escape((text or "").strip())
    return escaped.replace("\n", "<br/>")


def get_reply_review_status(session: Session, *, email_id: str) -> dict[str, Any]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")
    reply = get_current_reply_suggestion(session, email_id)
    if reply is None:
        raise ValueError(f"reply suggestion missing for email {email_id}")
    latest_write = get_latest_reply_draft_write(session, email_id)
    pending_review = bool(
        latest_write
        and latest_write.reply_suggestion_id == reply.id
        and latest_write.draft_status == "pending_review"
    )
    return {
        "email_id": email.email_id,
        "user_id": email.user_id,
        "reply_suggestion_id": reply.id,
        "reply_required": reply.reply_required,
        "decision_reason": reply.decision_reason,
        "tone_templates": dict(reply.tone_templates or {}),
        "review_required": bool(reply.reply_required),
        "pending_review": pending_review,
        "latest_draft_write": _serialize_draft_write(latest_write),
    }


def submit_reply_review(session: Session, *, email_id: str, body: ReplyReviewRequest) -> dict[str, Any]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")
    reply = get_current_reply_suggestion(session, email_id)
    if reply is None:
        raise ValueError(f"reply suggestion missing for email {email_id}")
    if body.reply_suggestion_id != reply.id:
        raise ValueError(f"reply_suggestion_id does not match current reply suggestion for email {email_id}")
    if not reply.reply_required:
        raise ValueError(f"reply review is not required for email {email_id}")

    latest_write = get_latest_reply_draft_write(session, email_id)
    if latest_write and latest_write.reply_suggestion_id == reply.id and latest_write.draft_status in TERMINAL_REVIEW_STATUSES:
        return {
            "email_id": email.email_id,
            "user_id": email.user_id,
            "reply_suggestion_id": reply.id,
            "action": body.action,
            "feedback_signal": "accepted" if latest_write.draft_status == "written" else latest_write.draft_status,
            "draft_status": latest_write.draft_status,
            "policy_name": latest_write.policy_name,
            "outlook_draft_id": latest_write.outlook_draft_id,
            "outlook_web_link": latest_write.outlook_web_link,
            "error_message": latest_write.error_message,
            "pending_review": False,
            "preference_vector": dict(getattr(get_user_writing_profile(session, email.user_id), "preference_vector", None) or {}),
        }

    profile = get_user_writing_profile(session, email.user_id)
    tone_templates = dict(reply.tone_templates or {})

    if body.action == ReplyReviewAction.approve:
        tone_key = body.tone_key or _preferred_tone_key(profile, tone_templates)
        body_text = (body.edited_body or "").strip() or tone_templates.get(tone_key, "").strip()
        if not body_text:
            raise ValueError("approved review requires a non-empty edited_body or valid tone_key")
        draft = create_reply_draft(
            session,
            user_id=email.user_id,
            message_id=email.graph_message_id or email.email_id,
            body_html=_render_body_html(body_text),
        )
        record = create_reply_draft_write(
            session,
            reply_suggestion_id=reply.id,
            user_id=email.user_id,
            email_id=email.email_id,
            policy_name="human_review_approved",
            draft_status="written",
            outlook_draft_id=draft.get("id"),
            outlook_web_link=draft.get("webLink"),
        )
        feedback_signal = "edited" if (body.edited_body or "").strip() else "accepted"
        feedback_metadata = {
            "tone_key": tone_key,
            "review_action": body.action.value,
            "draft_status": record.draft_status,
        }
    elif body.action == ReplyReviewAction.reject:
        record = create_reply_draft_write(
            session,
            reply_suggestion_id=reply.id,
            user_id=email.user_id,
            email_id=email.email_id,
            policy_name="human_review_rejected",
            draft_status="rejected",
        )
        feedback_signal = "rejected"
        feedback_metadata = {"review_action": body.action.value}
    else:
        record = create_reply_draft_write(
            session,
            reply_suggestion_id=reply.id,
            user_id=email.user_id,
            email_id=email.email_id,
            policy_name="human_review_deferred",
            draft_status="deferred",
        )
        feedback_signal = "deferred"
        feedback_metadata = {"review_action": body.action.value}

    create_feedback_event(
        session,
        user_id=email.user_id,
        email_id=email.email_id,
        target_type="reply_suggestion",
        target_id=str(reply.id),
        feedback_signal=feedback_signal,
        feedback_metadata=feedback_metadata,
    )
    preference_vector = update_preference_vector(session, email.user_id)
    return {
        "email_id": email.email_id,
        "user_id": email.user_id,
        "reply_suggestion_id": reply.id,
        "action": body.action,
        "feedback_signal": feedback_signal,
        "draft_status": record.draft_status,
        "policy_name": record.policy_name,
        "outlook_draft_id": record.outlook_draft_id,
        "outlook_web_link": record.outlook_web_link,
        "error_message": record.error_message,
        "pending_review": False,
        "preference_vector": preference_vector,
    }
