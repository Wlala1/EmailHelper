from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from agents.classification.common import ParsedAttachmentRecord
from models import AttachmentResult
from repositories import set_non_current_attachment


def save_attachment_results(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
    parsed_results: list[ParsedAttachmentRecord],
) -> None:
    for record in parsed_results:
        result = record.parsed
        # Attempt to mark any existing current result as non-current and insert
        # the new current result. Concurrent workers may race; retry once on
        # IntegrityError (Postgres partial unique index) and continue on failure.
        payload = dict(
            run_id=run_id,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            attachment_id=result.attachment_id,
            doc_type=result.doc_type,
            relevance_score=result.relevance_score,
            topics=result.topics,
            named_entities=result.named_entities,
            time_expressions=result.time_expressions,
            extracted_text=result.extracted_text,
            is_current=True,
        )

        try:
            with session.begin_nested():
                set_non_current_attachment(session, result.attachment_id)
                session.add(AttachmentResult(**payload))
                session.flush()
        except IntegrityError:
            session.rollback()
            try:
                with session.begin_nested():
                    set_non_current_attachment(session, result.attachment_id)
                    session.add(AttachmentResult(**payload))
                    session.flush()
            except IntegrityError:
                # If we still fail, another concurrent transaction won the race.
                # Skip inserting this record to avoid crashing the background worker.
                continue
