from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import CategorySuggestion


def get_category_suggestion(session: Session, suggestion_id: str) -> CategorySuggestion | None:
    return session.get(CategorySuggestion, suggestion_id)


def list_category_suggestions(
    session: Session,
    *,
    user_id: str,
    statuses: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[CategorySuggestion]:
    query = (
        select(CategorySuggestion)
        .where(CategorySuggestion.user_id == user_id)
        .order_by(CategorySuggestion.updated_at_utc.desc(), CategorySuggestion.created_at_utc.desc())
    )
    if statuses:
        query = query.where(CategorySuggestion.status.in_(tuple(statuses)))
    if limit is not None:
        query = query.limit(limit)
    return session.scalars(query).all()


def upsert_category_suggestion(
    session: Session,
    *,
    user_id: str,
    category_name: str,
    category_description: str,
    supporting_email_ids: list[str],
    supporting_subjects: list[str],
    rationale_keywords: list[str],
    sample_size: int,
    process_limit: int,
    created_from_email_id: str | None,
) -> CategorySuggestion:
    existing = session.scalars(
        select(CategorySuggestion)
        .where(
            CategorySuggestion.user_id == user_id,
            CategorySuggestion.category_name == category_name,
            CategorySuggestion.status.in_(("pending", "rejected")),
        )
        .order_by(CategorySuggestion.updated_at_utc.desc(), CategorySuggestion.created_at_utc.desc())
        .limit(1)
    ).first()
    if existing is None:
        existing = CategorySuggestion(
            suggestion_id=str(uuid4()),
            user_id=user_id,
            category_name=category_name,
            category_description=category_description,
            supporting_email_ids=supporting_email_ids,
            supporting_subjects=supporting_subjects,
            rationale_keywords=rationale_keywords,
            status="pending",
            sample_size=sample_size,
            process_limit=process_limit,
            created_from_email_id=created_from_email_id,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.category_description = category_description
    existing.supporting_email_ids = supporting_email_ids
    existing.supporting_subjects = supporting_subjects
    existing.rationale_keywords = rationale_keywords
    existing.sample_size = sample_size
    existing.process_limit = process_limit
    existing.created_from_email_id = created_from_email_id
    existing.status = "pending"
    existing.promoted_category_id = None
    existing.decided_at_utc = None
    session.flush()
    return existing


def set_category_suggestion_status(
    session: Session,
    suggestion: CategorySuggestion,
    *,
    status: str,
    promoted_category_id: str | None = None,
) -> CategorySuggestion:
    suggestion.status = status
    suggestion.promoted_category_id = promoted_category_id
    suggestion.decided_at_utc = datetime.now(timezone.utc)
    session.flush()
    return suggestion
