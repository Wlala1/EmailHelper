from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from config import LEASE_DURATION_SECONDS, POLL_INTERVAL_SECONDS
from db import SessionLocal
from models import UserMailboxState
from repositories import (
    acquire_lease,
    get_users_due_for_poll,
    get_users_pending_bootstrap,
    get_user_writing_profile,
    release_lease,
)
from services.calendar_feedback_service import sync_calendar_event_feedback
from services.mailbox_sync_service import MailboxSyncService
from services.writing_profile_service import rebuild_user_writing_profile

router = APIRouter(tags=["n8n"])

_sync_service = MailboxSyncService(SessionLocal)


def _with_lease(lock_name: str, callback):
    owner_id = f"n8n-{uuid4()}"
    with SessionLocal() as session:
        if not acquire_lease(
            session,
            lock_name=lock_name,
            owner_id=owner_id,
            lease_seconds=LEASE_DURATION_SECONDS,
        ):
            session.rollback()
            return {"status": "skipped", "reason": "lease_not_acquired"}
        session.commit()
    try:
        result = callback()
        return {"status": "success", **(result or {})}
    finally:
        with SessionLocal() as session:
            release_lease(session, lock_name=lock_name, owner_id=owner_id)
            session.commit()


@router.get("/v2/n8n/users_due_for_poll", response_model=dict)
def users_due_for_poll():
    with SessionLocal() as session:
        states = get_users_due_for_poll(session, poll_interval_seconds=POLL_INTERVAL_SECONDS)
        return {"user_ids": [state.user_id for state in states]}


@router.get("/v2/n8n/users_pending_bootstrap", response_model=dict)
def users_pending_bootstrap():
    with SessionLocal() as session:
        states = get_users_pending_bootstrap(session)
        return {"user_ids": [state.user_id for state in states]}


@router.get("/v2/n8n/active_users", response_model=dict)
def active_users():
    with SessionLocal() as session:
        states = session.scalars(
            select(UserMailboxState).where(UserMailboxState.mailbox_connected.is_(True))
        ).all()
        return {"user_ids": [state.user_id for state in states]}


@router.post("/v2/n8n/poll_user/{user_id}", response_model=dict)
def poll_user(user_id: str):
    try:
        return _with_lease(f"n8n:poll:{user_id}", lambda: _sync_service.poll_user(user_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v2/n8n/bootstrap_user/{user_id}", response_model=dict)
def bootstrap_user(user_id: str):
    try:
        return _with_lease(f"n8n:bootstrap:{user_id}", lambda: _sync_service.bootstrap_user(user_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v2/n8n/sync_calendar_feedback/{user_id}", response_model=dict)
def sync_calendar_feedback(user_id: str):
    try:
        return _with_lease(
            f"n8n:calendar-feedback:{user_id}",
            lambda: sync_calendar_event_feedback(SessionLocal, user_id=user_id),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v2/n8n/rebuild_profile/{user_id}", response_model=dict)
def rebuild_profile(user_id: str):
    def _rebuild() -> dict:
        with SessionLocal() as session:
            profile = rebuild_user_writing_profile(session, user_id)
            current_profile = get_user_writing_profile(session, user_id)
            session.commit()
            return {
                "user_id": user_id,
                "profile": profile,
                "preference_vector": dict(getattr(current_profile, "preference_vector", None) or {}),
            }

    try:
        return _with_lease(f"n8n:profile:{user_id}", _rebuild)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
