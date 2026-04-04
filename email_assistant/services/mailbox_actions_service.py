from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from services.graph_service import GraphServiceError, graph_service


def create_tentative_event(session: Session, *, user_id: str, candidate: dict[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    try:
        access_token = graph_service.ensure_access_token(session, user_id)
        event = graph_service.create_tentative_event(access_token, candidate)
        return "written", event.get("id"), event.get("webLink"), None
    except GraphServiceError as exc:
        return "failed", None, None, str(exc)
    except Exception as exc:
        return "failed", None, None, str(exc)


def create_reply_draft(
    session: Session,
    *,
    user_id: str,
    message_id: str,
    body_html: str,
) -> dict[str, Any]:
    access_token = graph_service.ensure_access_token(session, user_id)
    return graph_service.create_reply_draft(access_token, message_id, body_html=body_html)
