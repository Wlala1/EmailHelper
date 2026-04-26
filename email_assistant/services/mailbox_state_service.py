from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import DEFAULT_USER_TIMEZONE
from repositories import (
    get_or_create_user_mailbox_state,
    get_user,
    get_user_mailbox_account,
    get_user_writing_profile,
    mark_bootstrap_running,
    update_user_mailbox_state,
    upsert_user,
)
from db import SessionLocal
from schemas import MailboxConnectionResponse, UserModeStatusResponse, UserPayload
from services.graph_service import graph_service
from services.mailbox_sync_service import MailboxSyncService

logger = logging.getLogger(__name__)


def capture_baseline_tokens(access_token: str) -> tuple[str, str]:
    inbox_delta = graph_service.capture_delta_token(access_token, folder_name="inbox")
    sent_delta = graph_service.capture_delta_token(access_token, folder_name="sentitems")
    return inbox_delta, sent_delta


def build_mailbox_connection_response(session: Session, user_id: str) -> MailboxConnectionResponse:
    user = get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user_id not found: {user_id}")
    state = get_or_create_user_mailbox_state(session, user_id)
    return MailboxConnectionResponse(
        user_id=user.user_id,
        primary_email=user.primary_email,
        display_name=user.display_name,
        mailbox_connected=state.mailbox_connected,
        bootstrap_status=state.bootstrap_status,
        polling_enabled=state.polling_enabled,
        inbox_delta_token=state.inbox_delta_token,
        sent_delta_token=state.sent_delta_token,
    )


def build_user_status_response(session: Session, user_id: str) -> UserModeStatusResponse:
    user = get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user_id not found: {user_id}")
    state = get_or_create_user_mailbox_state(session, user_id)
    profile = get_user_writing_profile(session, user_id)
    active_mode = "steady_polling" if state.bootstrap_status == "completed" else "bootstrap_once"
    return UserModeStatusResponse(
        user_id=user.user_id,
        primary_email=user.primary_email,
        display_name=user.display_name,
        mailbox_connected=state.mailbox_connected,
        bootstrap_status=state.bootstrap_status,
        bootstrap_started_at_utc=state.bootstrap_started_at_utc,
        bootstrap_completed_at_utc=state.bootstrap_completed_at_utc,
        bootstrap_error=state.bootstrap_error,
        polling_enabled=state.polling_enabled,
        last_poll_at_utc=state.last_poll_at_utc,
        active_mode=active_mode,
        preferred_language=profile.preferred_language if profile else None,
        tone_profile=profile.tone_profile if profile else None,
        avg_length_bucket=profile.avg_length_bucket if profile else None,
        sample_count=profile.sample_count if profile else 0,
    )


def handle_microsoft_callback(session: Session, *, code: str, state: str | None = None) -> MailboxConnectionResponse:
    _ = state
    token_result = graph_service.exchange_code_for_token(code)
    access_token = token_result.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="Microsoft token exchange did not return access_token")

    profile = graph_service.fetch_user_profile(access_token)
    user_id = profile.get("id")
    if not user_id:
        raise HTTPException(status_code=502, detail="Microsoft profile did not return user id")

    user = upsert_user(
        session,
        UserPayload(
            user_id=user_id,
            primary_email=profile.get("mail") or profile.get("userPrincipalName"),
            display_name=profile.get("displayName"),
            timezone=DEFAULT_USER_TIMEZONE,
        ),
    )
    graph_service.persist_account_from_token(
        session,
        user_id=user_id,
        tenant_id=profile.get("tenantId") or "common",
        graph_user_id=profile.get("id"),
        token_result=token_result,
    )
    mailbox_state = get_or_create_user_mailbox_state(session, user_id)
    current_status = mailbox_state.bootstrap_status

    if current_status == "completed":
        inbox_delta = mailbox_state.inbox_delta_token
        sent_delta = mailbox_state.sent_delta_token
        if not inbox_delta or not sent_delta:
            inbox_delta, sent_delta = capture_baseline_tokens(access_token)
        update_user_mailbox_state(
            session,
            user_id=user_id,
            mailbox_connected=True,
            polling_enabled=True,
            inbox_delta_token=inbox_delta,
            sent_delta_token=sent_delta,
            bootstrap_error="",
        )
    elif current_status == "failed":
        update_user_mailbox_state(session, user_id=user_id, mailbox_connected=True, polling_enabled=True)
    elif current_status == "running":
        inbox_delta = mailbox_state.inbox_delta_token
        sent_delta = mailbox_state.sent_delta_token
        if not inbox_delta or not sent_delta:
            inbox_delta, sent_delta = capture_baseline_tokens(access_token)
        update_user_mailbox_state(
            session,
            user_id=user_id,
            mailbox_connected=True,
            polling_enabled=True,
            inbox_delta_token=inbox_delta,
            sent_delta_token=sent_delta,
        )
    else:
        inbox_delta, sent_delta = capture_baseline_tokens(access_token)
        mark_bootstrap_running(session, user_id)
        update_user_mailbox_state(session, user_id=user_id, inbox_delta_token=inbox_delta, sent_delta_token=sent_delta)

    session.commit()

    return build_mailbox_connection_response(session, user.user_id)


def retry_bootstrap(session: Session, *, user_id: str) -> UserModeStatusResponse:
    user = get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user_id not found: {user_id}")
    account = get_user_mailbox_account(session, user_id)
    if account is None:
        raise HTTPException(status_code=409, detail="mailbox account not connected")

    mailbox_state = get_or_create_user_mailbox_state(session, user_id)
    if mailbox_state.bootstrap_status == "completed":
        raise HTTPException(status_code=409, detail="bootstrap already completed")
    if mailbox_state.bootstrap_status == "running":
        raise HTTPException(status_code=409, detail="bootstrap already running")

    access_token = graph_service.ensure_access_token(session, user_id)
    inbox_delta, sent_delta = capture_baseline_tokens(access_token)
    mark_bootstrap_running(session, user_id)
    update_user_mailbox_state(
        session,
        user_id=user_id,
        inbox_delta_token=inbox_delta,
        sent_delta_token=sent_delta,
    )
    session.commit()
    return build_user_status_response(session, user_id)
