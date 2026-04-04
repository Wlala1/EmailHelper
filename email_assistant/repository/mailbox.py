from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import Email, SyncRun, SystemLease, User, UserMailboxAccount, UserMailboxState, UserWritingProfile
from schemas import BootstrapStatus
from utils import ensure_utc, utcnow


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_mailbox_account(session: Session, user_id: str) -> UserMailboxAccount | None:
    return session.get(UserMailboxAccount, user_id)


def upsert_user_mailbox_account(
    session: Session,
    *,
    user_id: str,
    tenant_id: str,
    graph_user_id: str,
    token_blob: dict[str, Any],
    token_expires_at_utc: Optional[datetime],
    scopes: list[str],
) -> UserMailboxAccount:
    existing = session.get(UserMailboxAccount, user_id)
    if existing is None:
        existing = UserMailboxAccount(
            user_id=user_id,
            provider="ms_graph",
            tenant_id=tenant_id,
            graph_user_id=graph_user_id,
            token_blob=token_blob,
            token_expires_at_utc=token_expires_at_utc,
            scopes=scopes,
        )
        session.add(existing)
    else:
        existing.provider = "ms_graph"
        existing.tenant_id = tenant_id
        existing.graph_user_id = graph_user_id
        existing.token_blob = token_blob
        existing.token_expires_at_utc = token_expires_at_utc
        existing.scopes = scopes
    session.flush()
    return existing


def get_or_create_user_mailbox_state(session: Session, user_id: str) -> UserMailboxState:
    state = session.get(UserMailboxState, user_id)
    if state is None:
        state = UserMailboxState(user_id=user_id)
        session.add(state)
        session.flush()
    return state


def update_user_mailbox_state(
    session: Session,
    *,
    user_id: str,
    mailbox_connected: Optional[bool] = None,
    bootstrap_status: Optional[BootstrapStatus | str] = None,
    bootstrap_started_at_utc: Optional[datetime] = None,
    bootstrap_completed_at_utc: Optional[datetime] = None,
    bootstrap_error: Optional[str] = None,
    polling_enabled: Optional[bool] = None,
    last_poll_at_utc: Optional[datetime] = None,
    inbox_delta_token: Optional[str] = None,
    sent_delta_token: Optional[str] = None,
) -> UserMailboxState:
    state = get_or_create_user_mailbox_state(session, user_id)
    if mailbox_connected is not None:
        state.mailbox_connected = mailbox_connected
    if bootstrap_status is not None:
        state.bootstrap_status = bootstrap_status.value if isinstance(bootstrap_status, BootstrapStatus) else bootstrap_status
    if bootstrap_started_at_utc is not None:
        state.bootstrap_started_at_utc = bootstrap_started_at_utc
    if bootstrap_completed_at_utc is not None:
        state.bootstrap_completed_at_utc = bootstrap_completed_at_utc
    if bootstrap_error is not None or bootstrap_error == "":
        state.bootstrap_error = bootstrap_error
    if polling_enabled is not None:
        state.polling_enabled = polling_enabled
    if last_poll_at_utc is not None:
        state.last_poll_at_utc = last_poll_at_utc
    if inbox_delta_token is not None:
        state.inbox_delta_token = inbox_delta_token
    if sent_delta_token is not None:
        state.sent_delta_token = sent_delta_token
    session.flush()
    return state


def mark_bootstrap_running(session: Session, user_id: str) -> UserMailboxState:
    return update_user_mailbox_state(
        session,
        user_id=user_id,
        mailbox_connected=True,
        bootstrap_status=BootstrapStatus.running,
        bootstrap_started_at_utc=utcnow(),
        bootstrap_completed_at_utc=None,
        bootstrap_error="",
        polling_enabled=True,
    )


def mark_bootstrap_completed(session: Session, user_id: str) -> UserMailboxState:
    return update_user_mailbox_state(
        session,
        user_id=user_id,
        bootstrap_status=BootstrapStatus.completed,
        bootstrap_completed_at_utc=utcnow(),
        bootstrap_error="",
        polling_enabled=True,
    )


def mark_bootstrap_failed(session: Session, user_id: str, error_message: str) -> UserMailboxState:
    return update_user_mailbox_state(
        session,
        user_id=user_id,
        bootstrap_status=BootstrapStatus.failed,
        bootstrap_error=error_message,
        polling_enabled=True,
    )


def get_users_pending_bootstrap(session: Session) -> list[UserMailboxState]:
    return session.scalars(
        select(UserMailboxState)
        .where(UserMailboxState.bootstrap_status == BootstrapStatus.running.value)
        .order_by(UserMailboxState.updated_at_utc.asc())
    ).all()


def get_users_due_for_poll(session: Session, *, poll_interval_seconds: int) -> list[UserMailboxState]:
    cutoff = utcnow() - timedelta(seconds=poll_interval_seconds)
    return session.scalars(
        select(UserMailboxState)
        .where(
            UserMailboxState.mailbox_connected.is_(True),
            UserMailboxState.polling_enabled.is_(True),
            UserMailboxState.bootstrap_status.in_(
                [BootstrapStatus.running.value, BootstrapStatus.completed.value, BootstrapStatus.failed.value]
            ),
            (UserMailboxState.last_poll_at_utc.is_(None) | (UserMailboxState.last_poll_at_utc <= cutoff)),
        )
        .order_by(UserMailboxState.last_poll_at_utc.asc().nullsfirst())
    ).all()


def update_poll_timestamp(session: Session, user_id: str, *, when: Optional[datetime] = None) -> UserMailboxState:
    return update_user_mailbox_state(session, user_id=user_id, last_poll_at_utc=when or utcnow())


def get_user_writing_profile(session: Session, user_id: str) -> UserWritingProfile | None:
    return session.get(UserWritingProfile, user_id)


def upsert_user_writing_profile(
    session: Session,
    *,
    user_id: str,
    preferred_language: Optional[str],
    tone_profile: Optional[str],
    avg_length_bucket: Optional[str],
    greeting_patterns: list[str],
    closing_patterns: list[str],
    signature_blocks: list[str],
    cta_patterns: list[str],
    sample_count: int,
    profile_payload: dict[str, Any],
) -> UserWritingProfile:
    profile = session.get(UserWritingProfile, user_id)
    if profile is None:
        profile = UserWritingProfile(
            user_id=user_id,
            preferred_language=preferred_language,
            tone_profile=tone_profile,
            avg_length_bucket=avg_length_bucket,
            greeting_patterns=greeting_patterns,
            closing_patterns=closing_patterns,
            signature_blocks=signature_blocks,
            cta_patterns=cta_patterns,
            sample_count=sample_count,
            profile_payload=profile_payload,
            last_profiled_at_utc=utcnow(),
        )
        session.add(profile)
    else:
        profile.preferred_language = preferred_language
        profile.tone_profile = tone_profile
        profile.avg_length_bucket = avg_length_bucket
        profile.greeting_patterns = greeting_patterns
        profile.closing_patterns = closing_patterns
        profile.signature_blocks = signature_blocks
        profile.cta_patterns = cta_patterns
        profile.sample_count = sample_count
        profile.profile_payload = profile_payload
        profile.last_profiled_at_utc = utcnow()
    session.flush()
    return profile


def get_recent_sent_emails(session: Session, user_id: str, *, limit: int) -> list[Email]:
    return session.scalars(
        select(Email)
        .where(Email.user_id == user_id, Email.direction == "outbound", Email.mailbox_folder == "sent")
        .order_by(Email.received_at_utc.desc())
        .limit(limit)
    ).all()


def create_sync_run(
    session: Session,
    *,
    user_id: str,
    sync_type: str,
    status: str,
    cursor_before: Optional[str] = None,
    cursor_after: Optional[str] = None,
    items_seen: int = 0,
    items_processed: int = 0,
    items_failed: int = 0,
    error_message: Optional[str] = None,
    run_metadata: Optional[dict[str, Any]] = None,
) -> SyncRun:
    run = SyncRun(
        user_id=user_id,
        sync_type=sync_type,
        status=status,
        cursor_before=cursor_before,
        cursor_after=cursor_after,
        items_seen=items_seen,
        items_processed=items_processed,
        items_failed=items_failed,
        error_message=error_message,
        run_metadata=run_metadata or {},
        completed_at_utc=utcnow() if status in {"success", "failed", "skipped"} else None,
    )
    session.add(run)
    session.flush()
    return run


def finalize_sync_run(
    session: Session,
    sync_run_id: int,
    *,
    status: str,
    cursor_after: Optional[str] = None,
    items_seen: Optional[int] = None,
    items_processed: Optional[int] = None,
    items_failed: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    values: dict[str, Any] = {"status": status, "completed_at_utc": utcnow()}
    if cursor_after is not None:
        values["cursor_after"] = cursor_after
    if items_seen is not None:
        values["items_seen"] = items_seen
    if items_processed is not None:
        values["items_processed"] = items_processed
    if items_failed is not None:
        values["items_failed"] = items_failed
    if error_message is not None:
        values["error_message"] = error_message
    session.execute(update(SyncRun).where(SyncRun.id == sync_run_id).values(**values))


def acquire_lease(session: Session, *, lock_name: str, owner_id: str, lease_seconds: int) -> bool:
    existing = session.get(SystemLease, lock_name)
    now = utcnow()
    locked_until = now + timedelta(seconds=lease_seconds)
    if existing is None:
        session.add(SystemLease(lock_name=lock_name, owner_id=owner_id, locked_until_utc=locked_until))
        session.flush()
        return True

    existing_locked_until = ensure_utc(existing.locked_until_utc)
    if existing_locked_until is None or existing_locked_until <= now or existing.owner_id == owner_id:
        existing.owner_id = owner_id
        existing.locked_until_utc = locked_until
        session.flush()
        return True
    return False


def release_lease(session: Session, *, lock_name: str, owner_id: str) -> None:
    existing = session.get(SystemLease, lock_name)
    if existing is None or existing.owner_id != owner_id:
        return
    existing.locked_until_utc = utcnow()
    session.flush()
