from __future__ import annotations

import threading
from uuid import uuid4

from sqlalchemy.orm import Session

from config import (
    BACKGROUND_LOOP_INTERVAL_SECONDS,
    ENABLE_BACKGROUND_WORKERS,
    LEASE_DURATION_SECONDS,
    POLL_INTERVAL_SECONDS,
)
from db import SessionLocal
from repositories import (
    acquire_lease,
    get_users_due_for_poll,
    get_users_pending_bootstrap,
    release_lease,
)
from services.mailbox_sync_service import MailboxSyncService


class MailboxWorker:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.owner_id = f"worker-{uuid4()}"
        self.sync_service = MailboxSyncService(SessionLocal)

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
            self._stop.wait(BACKGROUND_LOOP_INTERVAL_SECONDS)

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
