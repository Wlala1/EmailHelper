from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from config import BOOTSTRAP_INBOX_SAMPLE_SIZE, BOOTSTRAP_LOOKBACK_DAYS
from repositories import (
    create_sync_run,
    finalize_sync_run,
    get_email_by_graph_immutable_id,
    get_or_create_user_mailbox_state,
    get_user,
    get_user_mailbox_account,
    mark_bootstrap_completed,
    mark_bootstrap_failed,
    update_poll_timestamp,
    update_user_mailbox_state,
)
from services.graph_service import graph_service, parse_graph_datetime
from services.orchestration import (
    build_graph_intake_payload,
    execute_intake,
    learn_from_outbound_email,
    process_historical_inbox_email,
    process_live_inbox_email,
)
from utils import ensure_utc


class MailboxSyncService:
    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def bootstrap_user(self, user_id: str) -> dict[str, object]:
        session: Session = self.session_factory()
        try:
            user = get_user(session, user_id)
            account = get_user_mailbox_account(session, user_id)
            state = get_or_create_user_mailbox_state(session, user_id)
            if user is None or account is None or state.bootstrap_status != "running":
                session.commit()
                return {"user_id": user_id, "status": "skipped", "reason": "bootstrap_not_running"}
            primary_email = user.primary_email
            display_name = user.display_name
            timezone_name = user.timezone
            access_token = graph_service.ensure_access_token(session, user_id)
            session.commit()
        finally:
            session.close()

        since_utc = datetime.now(timezone.utc) - timedelta(days=BOOTSTRAP_LOOKBACK_DAYS)
        try:
            inbox_result = self._bootstrap_folder(
                user_id=user_id,
                primary_email=primary_email,
                display_name=display_name,
                timezone_name=timezone_name,
                access_token=access_token,
                folder_name="inbox",
                sync_type="bootstrap_inbox",
                processor="historical_inbox",
                since_utc=since_utc,
            )
            sent_result = self._bootstrap_folder(
                user_id=user_id,
                primary_email=primary_email,
                display_name=display_name,
                timezone_name=timezone_name,
                access_token=access_token,
                folder_name="sentitems",
                sync_type="bootstrap_sent",
                processor="historical_sent",
                since_utc=since_utc,
            )
            with self.session_factory() as session:
                mark_bootstrap_completed(session, user_id)
                session.commit()
            return {
                "user_id": user_id,
                "bootstrap_status": "completed",
                "folders": {
                    "inbox": inbox_result,
                    "sentitems": sent_result,
                },
            }
        except Exception as exc:
            with self.session_factory() as session:
                mark_bootstrap_failed(session, user_id, str(exc))
                session.commit()
            raise

    def poll_user(self, user_id: str) -> dict[str, object]:
        session: Session = self.session_factory()
        try:
            user = get_user(session, user_id)
            account = get_user_mailbox_account(session, user_id)
            state = get_or_create_user_mailbox_state(session, user_id)
            if user is None or account is None or not state.polling_enabled:
                session.commit()
                return {"user_id": user_id, "status": "skipped", "reason": "polling_disabled"}
            primary_email = user.primary_email
            display_name = user.display_name
            timezone_name = user.timezone
            access_token = graph_service.ensure_access_token(session, user_id)
            inbox_delta = state.inbox_delta_token
            sent_delta = state.sent_delta_token
            session.commit()
        finally:
            session.close()

        inbox_result = self._poll_folder(
            user_id=user_id,
            primary_email=primary_email,
            display_name=display_name,
            timezone_name=timezone_name,
            access_token=access_token,
            folder_name="inbox",
            delta_token=inbox_delta,
            sync_type="poll_inbox",
            process_mode="live",
        )
        sent_result = self._poll_folder(
            user_id=user_id,
            primary_email=primary_email,
            display_name=display_name,
            timezone_name=timezone_name,
            access_token=access_token,
            folder_name="sentitems",
            delta_token=sent_delta,
            sync_type="poll_sent",
            process_mode="sent_delta",
        )

        with self.session_factory() as session:
            update_poll_timestamp(session, user_id)
            session.commit()
        return {
            "user_id": user_id,
            "status": "completed",
            "folders": {
                "inbox": inbox_result,
                "sentitems": sent_result,
            },
        }

    def _bootstrap_folder(
        self,
        *,
        user_id: str,
        primary_email: str | None,
        display_name: str | None,
        timezone_name: str | None,
        access_token: str,
        folder_name: str,
        sync_type: str,
        processor: str,
        since_utc: datetime,
    ) -> dict[str, object]:
        with self.session_factory() as session:
            sync_run = create_sync_run(session, user_id=user_id, sync_type=sync_type, status="started")
            sync_run_id = sync_run.id
            session.commit()

        items_seen = 0
        items_processed = 0
        items_failed = 0
        try:
            messages = graph_service.list_messages_since(access_token, folder_name=folder_name, since_utc=since_utc)
            items_seen = len(messages)

            if processor == "historical_inbox" and len(messages) > BOOTSTRAP_INBOX_SAMPLE_SIZE:
                messages = random.sample(messages, BOOTSTRAP_INBOX_SAMPLE_SIZE)

            if processor == "historical_inbox":
                # Pass 1: intake all emails, collect (trace_id, email_id) pairs
                # Skip attachment fetching — classifier ignores attachments for bootstrap emails.
                intake_pairs: list[tuple[str, str]] = []
                for message in messages:
                    email_id, payload = build_graph_intake_payload(
                        user_id=user_id,
                        primary_email=primary_email,
                        display_name=display_name,
                        timezone_name=timezone_name,
                        message=message,
                        folder=folder_name,
                        processed_mode="bootstrap",
                        attachments=[],
                    )
                    trace_id = str(uuid4())
                    execute_intake(self.session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id, payload=payload)
                    intake_pairs.append((trace_id, email_id))
                    items_processed += 1

                # Discovery: one batch LLM call on all stored email subjects → 5–8 broad categories
                from services.batch_backfill_service import discover_bootstrap_categories
                catalog = discover_bootstrap_categories(self.session_factory, user_id=user_id)

                # Pass 2: classify all in parallel — each call is independent (own DB session + LLM call)
                def _classify(pair: tuple[str, str]) -> bool:
                    t_id, e_id = pair
                    try:
                        process_historical_inbox_email(
                            self.session_factory,
                            trace_id=t_id,
                            email_id=e_id,
                            user_id=user_id,
                            category_catalog_override=catalog,
                        )
                        return True
                    except Exception:
                        return False

                with ThreadPoolExecutor(max_workers=10) as pool:
                    futures = {pool.submit(_classify, pair): pair for pair in intake_pairs}
                    for future in as_completed(futures):
                        if not future.result():
                            items_failed += 1

                # Batch Neo4j sync — one transaction for all bootstrap observations
                from agents.relationship_graph_agent import batch_sync_neo4j_for_user
                with self.session_factory() as session:
                    batch_sync_neo4j_for_user(session, user_id=user_id)
            else:
                for message in messages:
                    attachments = graph_service.fetch_attachments(access_token, message.get("id")) if message.get("hasAttachments") else []
                    email_id, payload = build_graph_intake_payload(
                        user_id=user_id,
                        primary_email=primary_email,
                        display_name=display_name,
                        timezone_name=timezone_name,
                        message=message,
                        folder=folder_name,
                        processed_mode="bootstrap",
                        attachments=attachments,
                    )
                    trace_id = str(uuid4())
                    execute_intake(self.session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id, payload=payload)
                    learn_from_outbound_email(self.session_factory, email_id=email_id, user_id=user_id)
                    items_processed += 1

            with self.session_factory() as session:
                finalize_sync_run(
                    session,
                    sync_run_id,
                    status="success",
                    items_seen=items_seen,
                    items_processed=items_processed,
                    items_failed=items_failed,
                )
                session.commit()
            return {
                "sync_type": sync_type,
                "status": "success",
                "items_seen": items_seen,
                "items_processed": items_processed,
                "items_failed": items_failed,
            }
        except Exception as exc:
            with self.session_factory() as session:
                finalize_sync_run(
                    session,
                    sync_run_id,
                    status="failed",
                    items_seen=items_seen,
                    items_processed=items_processed,
                    items_failed=items_failed + 1,
                    error_message=str(exc),
                )
                session.commit()
            raise

    def _poll_folder(
        self,
        *,
        user_id: str,
        primary_email: str | None,
        display_name: str | None,
        timezone_name: str | None,
        access_token: str,
        folder_name: str,
        delta_token: str | None,
        sync_type: str,
        process_mode: str,
    ) -> dict[str, object]:
        with self.session_factory() as session:
            sync_run = create_sync_run(
                session,
                user_id=user_id,
                sync_type=sync_type,
                status="started",
                cursor_before=delta_token,
            )
            sync_run_id = sync_run.id
            session.commit()

        items_seen = 0
        items_processed = 0
        items_failed = 0
        next_delta_token = delta_token
        try:
            messages, next_delta_token = graph_service.delta_messages(access_token, folder_name=folder_name, delta_token=delta_token)
            items_seen = len(messages)
            for message in messages:
                if message.get("@removed"):
                    continue
                existing = None
                with self.session_factory() as session:
                    message_id = message.get("id") or message.get("internetMessageId")
                    if message_id:
                        existing = get_email_by_graph_immutable_id(session, user_id=user_id, graph_immutable_id=message_id)
                modified_at = parse_graph_datetime(message.get("lastModifiedDateTime"))
                if existing and ensure_utc(existing.mailbox_last_modified_at_utc) == modified_at:
                    continue
                attachments = graph_service.fetch_attachments(access_token, message.get("id")) if message.get("hasAttachments") else []
                email_id, payload = build_graph_intake_payload(
                    user_id=user_id,
                    primary_email=primary_email,
                    display_name=display_name,
                    timezone_name=timezone_name,
                    message=message,
                    folder=folder_name,
                    processed_mode="live",
                    attachments=attachments,
                )
                trace_id = str(uuid4())
                execute_intake(self.session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id, payload=payload)
                if process_mode == "live":
                    process_live_inbox_email(self.session_factory, trace_id=trace_id, email_id=email_id, user_id=user_id)
                else:
                    learn_from_outbound_email(self.session_factory, email_id=email_id, user_id=user_id)
                items_processed += 1

            with self.session_factory() as session:
                update_user_mailbox_state(
                    session,
                    user_id=user_id,
                    inbox_delta_token=next_delta_token if folder_name == "inbox" else None,
                    sent_delta_token=next_delta_token if folder_name == "sentitems" else None,
                )
                finalize_sync_run(
                    session,
                    sync_run_id,
                    status="success",
                    cursor_after=next_delta_token,
                    items_seen=items_seen,
                    items_processed=items_processed,
                    items_failed=items_failed,
                )
                session.commit()
            return {
                "sync_type": sync_type,
                "status": "success",
                "cursor_after": next_delta_token,
                "items_seen": items_seen,
                "items_processed": items_processed,
                "items_failed": items_failed,
            }
        except Exception as exc:
            with self.session_factory() as session:
                finalize_sync_run(
                    session,
                    sync_run_id,
                    status="failed",
                    cursor_after=next_delta_token,
                    items_seen=items_seen,
                    items_processed=items_processed,
                    items_failed=items_failed + 1,
                    error_message=str(exc),
                )
                session.commit()
            raise
