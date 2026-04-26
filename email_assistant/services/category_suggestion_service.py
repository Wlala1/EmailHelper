from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from agents.classification.common import normalize_category_name, tokenize, truncate_chars
from config import CATEGORY_SUGGESTION_BACKFILL_LIMIT
from repositories import (
    create_category_definition,
    get_category_definitions,
    get_category_suggestion,
    get_recent_emails_for_user,
    get_unclassified_emails_for_user,
    list_category_suggestions,
    set_category_suggestion_status,
    upsert_category_suggestion,
)
from services.batch_backfill_service import classify_backlog_for_user, generate_dynamic_topics


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _email_text(email: Any) -> str:
    body = email.body_preview or ""
    if not body and email.body_content:
        body = _strip_html(email.body_content)
    return f"{email.subject or ''}\n{body}"


def _supporting_context(topic: dict[str, str], sample_emails: list[Any], fallback_offset: int = 0) -> tuple[list[str], list[str], list[str]]:
    topic_tokens = tokenize(f"{topic['category_name']} {topic['category_description']}")
    scored: list[tuple[int, int, Any]] = []
    for index, email in enumerate(sample_emails):
        email_tokens = tokenize(_email_text(email))
        score = len(topic_tokens & email_tokens)
        heuristic_hint = normalize_category_name(topic["category_name"])
        if heuristic_hint and heuristic_hint.lower() in (email.subject or "").lower():
            score += 2
        scored.append((score, -index, email))
    scored.sort(reverse=True)
    supporting = [item[-1] for item in scored if item[0] > 0][:4]
    if not supporting and sample_emails:
        # Stagger the fallback slice so each topic gets different supporting emails
        start = fallback_offset % len(sample_emails)
        supporting = sample_emails[start:start + 3] or sample_emails[:3]
    keyword_counter: Counter[str] = Counter()
    for email in supporting:
        keyword_counter.update(tokenize(_email_text(email)))
    keywords = [token for token, _ in keyword_counter.most_common(6)]
    return (
        [email.email_id for email in supporting],
        [truncate_chars(email.subject or "(No Subject)", 120) for email in supporting],
        keywords,
    )


def serialize_category_suggestion(suggestion: Any) -> dict[str, Any]:
    return {
        "suggestion_id": suggestion.suggestion_id,
        "user_id": suggestion.user_id,
        "category_name": suggestion.category_name,
        "category_description": suggestion.category_description,
        "supporting_email_ids": list(suggestion.supporting_email_ids or []),
        "supporting_subjects": list(suggestion.supporting_subjects or []),
        "rationale_keywords": list(suggestion.rationale_keywords or []),
        "status": suggestion.status,
        "sample_size": int(suggestion.sample_size or 0),
        "process_limit": int(suggestion.process_limit or 0),
        "created_from_email_id": suggestion.created_from_email_id,
        "promoted_category_id": suggestion.promoted_category_id,
        "decided_at_utc": suggestion.decided_at_utc,
        "created_at_utc": suggestion.created_at_utc,
        "updated_at_utc": suggestion.updated_at_utc,
    }


def list_category_suggestions_for_user(
    session: Session,
    *,
    user_id: str,
    statuses: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    suggestions = list_category_suggestions(session, user_id=user_id, statuses=statuses, limit=limit)
    return {
        "user_id": user_id,
        "suggestions": [serialize_category_suggestion(item) for item in suggestions],
    }


def generate_category_suggestions_for_user(
    session_factory: sessionmaker,
    *,
    user_id: str,
    sample_size: int,
    process_limit: int,
) -> dict[str, Any]:
    limit = max(sample_size, process_limit)
    with session_factory() as session:
        backlog = get_unclassified_emails_for_user(session, user_id, limit=limit)
        if not backlog:
            # All emails are classified — sample recent emails for topic discovery instead
            backlog = get_recent_emails_for_user(session, user_id, limit=limit)
        if not backlog:
            return {
                "status": "success",
                "reason": "no_emails",
                "user_id": user_id,
                "sample_size": sample_size,
                "process_limit": process_limit,
                "generated_count": 0,
                "suggestions": [],
            }

        sample_emails = backlog[:sample_size]
        discovered_topics = generate_dynamic_topics(sample_emails)
        existing_names = {item.category_name for item in get_category_definitions(session, user_id)}
        created_from_email_id = sample_emails[0].email_id if sample_emails else None
        suggestions: list[dict[str, Any]] = []
        for idx, topic in enumerate(discovered_topics):
            category_name = normalize_category_name(topic["category_name"])
            if category_name in existing_names:
                continue
            supporting_email_ids, supporting_subjects, rationale_keywords = _supporting_context(topic, sample_emails, fallback_offset=idx * 3)
            suggestion = upsert_category_suggestion(
                session,
                user_id=user_id,
                category_name=category_name,
                category_description=topic["category_description"],
                supporting_email_ids=supporting_email_ids,
                supporting_subjects=supporting_subjects,
                rationale_keywords=rationale_keywords,
                sample_size=sample_size,
                process_limit=process_limit,
                created_from_email_id=created_from_email_id,
            )
            suggestions.append(serialize_category_suggestion(suggestion))
        session.commit()
        return {
            "status": "success",
            "reason": None if suggestions else "all_topics_already_defined",
            "user_id": user_id,
            "sample_size": sample_size,
            "process_limit": process_limit,
            "generated_count": len(suggestions),
            "suggestions": suggestions,
        }


def decide_category_suggestion(
    session_factory: sessionmaker,
    *,
    suggestion_id: str,
    action: str,
) -> dict[str, Any]:
    with session_factory() as session:
        suggestion = get_category_suggestion(session, suggestion_id)
        if suggestion is None:
            raise ValueError(f"category suggestion not found: {suggestion_id}")

        if suggestion.status == "accepted":
            return {
                "user_id": suggestion.user_id,
                "suggestion": serialize_category_suggestion(suggestion),
                "backfill": {"status": "skipped", "reason": "already_accepted"},
            }
        if suggestion.status == "rejected" and action == "reject":
            return {
                "user_id": suggestion.user_id,
                "suggestion": serialize_category_suggestion(suggestion),
                "backfill": {"status": "skipped", "reason": "already_rejected"},
            }

        if action == "reject":
            set_category_suggestion_status(session, suggestion, status="rejected")
            session.commit()
            return {
                "user_id": suggestion.user_id,
                "suggestion": serialize_category_suggestion(suggestion),
                "backfill": {"status": "skipped", "reason": "rejected"},
            }

        if action != "accept":
            raise ValueError(f"unsupported category suggestion action: {action}")

        category = create_category_definition(
            session,
            user_id=suggestion.user_id,
            category_name=suggestion.category_name,
            category_description=suggestion.category_description,
            created_from_email_id=suggestion.created_from_email_id,
        )
        set_category_suggestion_status(
            session,
            suggestion,
            status="accepted",
            promoted_category_id=category.category_id,
        )
        category_catalog_override = [
            {
                "category_name": item.category_name,
                "category_description": item.category_description,
            }
            for item in get_category_definitions(session, suggestion.user_id)
        ]
        accepted_user_id = suggestion.user_id
        accepted_category_name = suggestion.category_name
        accepted_process_limit = suggestion.process_limit or CATEGORY_SUGGESTION_BACKFILL_LIMIT
        session.commit()

    # Bulk-update ClassifierResult rows that voted for this category
    from sqlalchemy import update as sa_update
    from models import ClassifierResult
    with session_factory() as session:
        session.execute(
            sa_update(ClassifierResult)
            .where(
                ClassifierResult.user_id == accepted_user_id,
                ClassifierResult.proposed_category_name == accepted_category_name,
                ClassifierResult.is_current.is_(True),
            )
            .values(category=accepted_category_name, proposed_category_name=None)
        )
        session.commit()

    backfill = classify_backlog_for_user(
        session_factory,
        user_id=accepted_user_id,
        process_limit=accepted_process_limit,
        category_catalog_override=category_catalog_override or None,
    )
    with session_factory() as session:
        refreshed = get_category_suggestion(session, suggestion_id)
        if refreshed is None:
            raise ValueError(f"category suggestion disappeared after update: {suggestion_id}")
        return {
            "user_id": refreshed.user_id,
            "suggestion": serialize_category_suggestion(refreshed),
            "backfill": {"status": "success", **backfill},
        }
