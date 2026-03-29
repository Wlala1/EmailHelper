from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from repositories import replace_recipients, upsert_attachments, upsert_email, upsert_user
from schemas import AttachmentPayload, EmailPayload, EmailRecipientPayload, UserPayload


def run_intake(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    user_payload = payload.get("user") or {"user_id": user_id}
    user_payload.setdefault("user_id", user_id)
    email_payload = payload.get("email") or {"email_id": email_id, "sender_email": "", "received_at_utc": datetime.now(timezone.utc)}
    email_payload.setdefault("email_id", email_id)

    user = UserPayload(**user_payload)
    email = EmailPayload(**email_payload)
    recipients = [EmailRecipientPayload(**x) for x in payload.get("email_recipients", [])]
    attachments = [AttachmentPayload(**x) for x in payload.get("attachments", [])]

    upsert_user(session, user)
    upsert_email(session, user.user_id, email)
    replace_recipients(session, email.email_id, recipients)
    saved_attachments = upsert_attachments(session, email.email_id, attachments)

    return {
        "user": user.model_dump(mode="json"),
        "email": email.model_dump(mode="json"),
        "email_recipients": [r.model_dump(mode="json") for r in recipients],
        "attachments": [
            {
                "attachment_id": a.attachment_id,
                "name": a.name,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "is_inline": a.is_inline,
                "local_path": a.local_path,
            }
            for a in saved_attachments
        ],
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
