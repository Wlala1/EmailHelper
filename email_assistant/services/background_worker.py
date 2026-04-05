from __future__ import annotations

import threading
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import (
    BACKGROUND_LOOP_INTERVAL_SECONDS,
    CATEGORY_SUGGESTION_INTERVAL_SECONDS,
    ENABLE_BACKGROUND_WORKERS,
    LEASE_DURATION_SECONDS,
    POLL_INTERVAL_SECONDS,
    PROFILE_REBUILD_INTERVAL_SECONDS,
)
from db import SessionLocal
from models import UserMailboxState
from repositories import (
    acquire_lease,
    get_users_due_for_poll,
    get_users_pending_bootstrap,
    release_lease,
)
from services.calendar_feedback_service import sync_calendar_event_feedback
from services.category_suggestion_service import generate_category_suggestions_for_user
from services.mailbox_sync_service import MailboxSyncService
from services.writing_profile_service import rebuild_user_writing_profile


class MailboxWorker:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.owner_id = f"worker-{uuid4()}"
        self.sync_service = MailboxSyncService(SessionLocal)
        self._last_profile_rebuild_at: float = 0.0
        self._last_category_suggestion_at: float = 0.0

    def start(self) -> None:
        if not ENABLE_BACKGROUND_WORKERS:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mailbox-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_bootstrap_cycle_once()
            self.run_poll_cycle_once()
            self.run_calendar_feedback_cycle_once()

            now = time.time()
            if now - self._last_profile_rebuild_at >= PROFILE_REBUILD_INTERVAL_SECONDS:
                self.run_writing_profile_rebuild_cycle_once()
                self._last_profile_rebuild_at = now

            if now - self._last_category_suggestion_at >= CATEGORY_SUGGESTION_INTERVAL_SECONDS:
                self.run_category_suggestion_cycle_once()
                self._last_category_suggestion_at = now

            self._stop.wait(BACKGROUND_LOOP_INTERVAL_SECONDS)

    def _get_active_user_ids(self) -> list[str]:
        with SessionLocal() as session:
            states = session.scalars(
                select(UserMailboxState).where(UserMailboxState.mailbox_connected.is_(True))
            ).all()
            return [s.user_id for s in states]

    def run_bootstrap_cycle_once(self) -> None:
        with SessionLocal() as session:
            user_ids = [state.user_id for state in get_users_pending_bootstrap(session)]
        for user_id in user_ids:
            self._with_user_lease(f"bootstrap:{user_id}", lambda uid=user_id: self.sync_service.bootstrap_user(uid))

    def run_poll_cycle_once(self, *, force: bool = False) -> None:
        session: Session = SessionLocal()
        try:
            if not force and not acquire_lease(
                session,
                lock_name="mailbox-poller",
                owner_id=self.owner_id,
                lease_seconds=LEASE_DURATION_SECONDS,
            ):
                session.rollback()
                return
            session.commit()
        finally:
            session.close()

        try:
            with SessionLocal() as session:
                user_ids = [
                    state.user_id
                    for state in get_users_due_for_poll(session, poll_interval_seconds=0 if force else POLL_INTERVAL_SECONDS)
                ]
            for user_id in user_ids:
                self.sync_service.poll_user(user_id)
        finally:
            with SessionLocal() as session:
                release_lease(session, lock_name="mailbox-poller", owner_id=self.owner_id)
                session.commit()

    def run_calendar_feedback_cycle_once(self) -> None:
        """Check Outlook event response statuses and record feedback signals."""
        with SessionLocal() as session:
            from repositories import get_users_due_for_poll
            user_ids = [
                state.user_id
                for state in get_users_due_for_poll(session, poll_interval_seconds=POLL_INTERVAL_SECONDS)
            ]
        for user_id in user_ids:
            self._with_user_lease(
                f"calendar-feedback:{user_id}",
                lambda uid=user_id: sync_calendar_event_feedback(SessionLocal, user_id=uid),
            )

    def run_writing_profile_rebuild_cycle_once(self) -> None:
        """Rebuild writing profile for all active users (runs every 24h)."""
        for user_id in self._get_active_user_ids():
            def _rebuild(uid=user_id):
                with SessionLocal() as session:
                    rebuild_user_writing_profile(session, uid)
                    session.commit()
            self._with_user_lease(f"profile-rebuild:{user_id}", _rebuild)

    def run_category_suggestion_cycle_once(self) -> None:
        """Refresh category suggestions for all active users (runs every 12h)."""
        for user_id in self._get_active_user_ids():
            self._with_user_lease(
                f"category-suggestion:{user_id}",
                lambda uid=user_id: generate_category_suggestions_for_user(
                    SessionLocal, user_id=uid, sample_size=50, process_limit=50
                ),
            )

    def _with_user_lease(self, lock_name: str, callback) -> None:
        session: Session = SessionLocal()
        try:
            if not acquire_lease(
                session,
                lock_name=lock_name,
                owner_id=self.owner_id,
                lease_seconds=LEASE_DURATION_SECONDS,
            ):
                session.rollback()
                return
            session.commit()
        finally:
            session.close()

        try:
            callback()
        finally:
            with SessionLocal() as session:
                release_lease(session, lock_name=lock_name, owner_id=self.owner_id)
                session.commit()


mailbox_worker = MailboxWorker()
