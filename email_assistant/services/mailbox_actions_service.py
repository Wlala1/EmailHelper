from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from services.graph_service import GraphServiceError, graph_service


def get_recent_calendar_events(
    session: Session,
    *,
    user_id: str,
    start_time_utc: datetime,
    end_time_utc: datetime,
) -> list[dict]:
    """Return calendar events in the given window from the user's Outlook calendar.

    Returns an empty list if the Graph API call fails.
    """
    try:
        access_token = graph_service.ensure_access_token(session, user_id)
        return graph_service.get_calendar_events(
            access_token,
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
        )
    except Exception:
        return []


def check_free_busy(
    session: Session,
    *,
    user_id: str,
    start_time_utc: datetime,
    end_time_utc: datetime,
) -> list[dict[str, Any]]:
    """Return free/busy schedule items for the user's calendar window.

    Returns an empty list if the Graph API call fails (e.g. no mailbox connected).
    """
    try:
        access_token = graph_service.ensure_access_token(session, user_id)
        return graph_service.get_free_busy(
            access_token,
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
        )
    except Exception:
        return []


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
