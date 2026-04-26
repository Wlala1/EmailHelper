from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from openai import OpenAI
from sqlalchemy.orm import sessionmaker

from agents.classification.common import normalize_category_name, truncate_chars
from agents.classification.heuristics import heuristic_new_category_description, heuristic_new_category_name
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from repositories import get_unclassified_emails_for_user
from services.orchestration import execute_classifier

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None

TOPIC_DISCOVERY_PROMPT = """You design broad, reusable email categories for a university student or researcher.

You receive a list of email subjects from the past 6 months.
Produce exactly 5 to 8 BROAD, GENERAL categories that together cover the entire inbox.
Categories must be as wide as possible — good examples:
  "Career Opportunities", "Course Notifications", "Seminars & Talks",
  "Research Opportunities", "Administrative Notices", "Social Events",
  "Billing & Payments", "General Updates"

Rules:
- Prefer broader categories over narrow, one-off ones.
- Every email should be able to fit into one of these categories.
- Always include "General Updates" as a catch-all for anything that doesn't fit elsewhere.
- Maximum 8 categories total.

Return JSON only:
{
  "topics": [
    {
      "category_name": "Career Opportunities",
      "category_description": "Internships, recruiting outreach, career fairs, and job applications."
    }
  ]
}
"""


def _email_snippet(email: Any) -> dict[str, str]:
    body = email.body_content or email.body_preview or ""
    return {
        "email_id": email.email_id,
        "subject": truncate_chars(email.subject or "", 160),
        "content": truncate_chars(body.strip(), 600),
    }


def _subject_snippet(email: Any) -> dict[str, str]:
    return {"subject": truncate_chars(email.subject or "(no subject)", 160)}


def _heuristic_topics(sample_emails: list[Any]) -> list[dict[str, str]]:
    topics: list[dict[str, str]] = []
    seen: set[str] = set()
    for email in sample_emails:
        combined = f"{email.subject or ''}\n{email.body_content or email.body_preview or ''}"
        name = normalize_category_name(heuristic_new_category_name(email.subject or "", combined))
        if not name or name in seen:
            continue
        seen.add(name)
        topics.append(
            {
                "category_name": name,
                "category_description": heuristic_new_category_description(name, combined),
            }
        )
        if len(topics) >= 8:
            break
    if topics:
        return topics
    return [
        {
            "category_name": "General Updates",
            "category_description": "General email updates that do not fit a more specific reusable category.",
        }
    ]


def _llm_topics_from_payload(payload: dict) -> list[dict[str, str]] | None:
    """Core LLM topic discovery call — accepts any pre-built payload dict."""
    if _client is None:
        return None
    try:
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": TOPIC_DISCOVERY_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        topics: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in data.get("topics", []):
            name = normalize_category_name(str(item.get("category_name", "")).strip())
            if not name or name in seen:
                continue
            description = str(item.get("category_description", "")).strip() or f"Emails related to {name}."
            seen.add(name)
            topics.append({"category_name": name, "category_description": description})
            if len(topics) >= 8:
                break
        return topics or None
    except Exception:
        return None


def _llm_topics(sample_emails: list[Any]) -> list[dict[str, str]] | None:
    if not sample_emails:
        return None
    return _llm_topics_from_payload({"emails": [_email_snippet(email) for email in sample_emails]})


def generate_dynamic_topics(sample_emails: list[Any]) -> list[dict[str, str]]:
    topics = _llm_topics(sample_emails)
    if topics:
        return topics
    return _heuristic_topics(sample_emails)


def discover_bootstrap_categories(
    session_factory: sessionmaker,
    *,
    user_id: str,
) -> list[dict[str, str]]:
    """One-time bootstrap category discovery.

    Sends ALL stored email subjects (no body content) to the LLM in a single
    call to discover 5-8 broad categories. Persists them as CategoryDefinition
    rows and returns the catalog list for use as category_catalog_override.
    """
    from repositories import get_recent_emails_for_user, get_category_definitions, create_category_definition

    with session_factory() as session:
        all_emails = get_recent_emails_for_user(session, user_id, limit=2000)
        existing_names = {c.category_name for c in get_category_definitions(session, user_id)}

    if not all_emails:
        return []

    # Subject-only payload — cheap, covers full 180-day range
    payload = {"emails": [_subject_snippet(e) for e in all_emails]}
    topics = _llm_topics_from_payload(payload) or _heuristic_topics(all_emails)

    with session_factory() as session:
        for topic in topics:
            name = normalize_category_name(topic["category_name"])
            if name not in existing_names:
                create_category_definition(
                    session,
                    user_id=user_id,
                    category_name=name,
                    category_description=topic["category_description"],
                    created_from_email_id=None,
                )
                existing_names.add(name)
        session.commit()
        all_cats = get_category_definitions(session, user_id)
        return [
            {"category_name": c.category_name, "category_description": c.category_description}
            for c in all_cats
        ]


def classify_backlog_for_user(
    session_factory: sessionmaker,
    *,
    user_id: str,
    process_limit: int,
    category_catalog_override: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    with session_factory() as session:
        backlog = get_unclassified_emails_for_user(session, user_id, limit=process_limit)
        target_email_ids = [email.email_id for email in backlog[:process_limit]]
    processed_email_ids: list[str] = []
    failed_email_ids: list[str] = []
    for email_id in target_email_ids:
        try:
            execute_classifier(
                session_factory,
                trace_id=str(uuid4()),
                email_id=email_id,
                user_id=user_id,
                category_catalog_override=category_catalog_override,
            )
        except Exception:
            failed_email_ids.append(email_id)
        else:
            processed_email_ids.append(email_id)

    return {
        "user_id": user_id,
        "process_limit": process_limit,
        "processed_email_ids": processed_email_ids,
        "failed_email_ids": failed_email_ids,
        "processed_count": len(processed_email_ids),
        "failed_count": len(failed_email_ids),
    }


def backfill_classifier_for_user(
    session_factory: sessionmaker,
    *,
    user_id: str,
    sample_size: int,
    process_limit: int,
) -> dict[str, Any]:
    with session_factory() as session:
        backlog = get_unclassified_emails_for_user(session, user_id, limit=max(sample_size, process_limit))
        sample_emails = backlog[:sample_size]
    topics = generate_dynamic_topics(sample_emails) if sample_emails else []
    result = classify_backlog_for_user(
        session_factory,
        user_id=user_id,
        process_limit=process_limit,
        category_catalog_override=topics or None,
    )
    return {
        "user_id": user_id,
        "sample_size": sample_size,
        "process_limit": process_limit,
        "topics": topics,
        **result,
    }
