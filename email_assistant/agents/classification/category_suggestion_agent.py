from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agents.classification.attachment_context import build_attachment_context
from agents.classification.common import build_combined_context, dedup_keep_order, normalize_category_name
from agents.classification.heuristics import best_existing_category, heuristic_classify
from agents.classification.llm import llm_classify, llm_classify_with_tools
from config import CATEGORY_SUGGESTION_BACKFILL_LIMIT
from models import ClassifierResult
from repositories import (
    get_category_definitions,
    get_email,
    set_non_current_classifier,
    upsert_category_suggestion,
)


def run_category_suggestion(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, object]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")

    existing_categories = [
        {"category_name": item.category_name, "category_description": item.category_description}
        for item in get_category_definitions(session, user_id)
    ]

    combined_text = build_combined_context(email, build_attachment_context([]))

    # Primary: tool-calling agent that actively searches categories
    model_output = llm_classify_with_tools(
        session,
        user_id=user_id,
        combined_text=combined_text,
        subject=email.subject or "",
    )
    # Fallback 1: structured LLM
    if model_output is None:
        model_output = llm_classify(
            combined_text=combined_text,
            existing_categories=existing_categories,
            subject=email.subject or "",
        )
    # Fallback 2: heuristics
    if model_output is None:
        model_output = heuristic_classify(
            combined_text=combined_text,
            sender_email=email.sender_email,
            sender_name=email.sender_name,
            subject=email.subject or "",
            existing_categories=existing_categories,
        )

    category_name = normalize_category_name(model_output["category_name"])
    category_description = str(model_output.get("category_description") or "").strip()
    existing_lookup = {item["category_name"]: item["category_description"] for item in existing_categories}

    is_new_category = bool(model_output.get("is_new_category", False))
    proposed_name: str | None = None

    if category_name in existing_lookup:
        category_description = existing_lookup[category_name]
        is_new_category = False
    elif is_new_category:
        proposed_name = normalize_category_name(model_output["category_name"])
        best_name, best_score = best_existing_category(existing_categories, combined_text)
        if best_score >= 0.25:
            proposed_name = None
        category_name = best_name or (existing_categories[0]["category_name"] if existing_categories else proposed_name)
        category_description = existing_lookup.get(category_name or "", "")
        is_new_category = False

    merged_entities = dedup_keep_order(
        [str(item) for item in model_output.get("named_entities", [])]
    )[:10]
    merged_times = dedup_keep_order(
        [str(item) for item in model_output.get("time_expressions", [])]
    )[:10]

    set_non_current_classifier(session, email_id)
    session.add(
        ClassifierResult(
            run_id=run_id,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            category=category_name,
            urgency_score=float(model_output.get("urgency_score", 0.5)),
            summary=str(model_output.get("summary", "")).strip() or "",
            named_entities=merged_entities,
            time_expressions=merged_times,
            is_current=True,
            proposed_category_name=proposed_name,
        )
    )
    session.flush()

    if proposed_name:
        upsert_category_suggestion(
            session,
            user_id=user_id,
            category_name=proposed_name,
            category_description=str(model_output.get("category_description") or "").strip()
                or f"Emails related to {proposed_name}.",
            supporting_email_ids=[email_id],
            supporting_subjects=[email.subject or "(No Subject)"],
            rationale_keywords=[str(e) for e in model_output.get("named_entities", [])][:10],
            sample_size=1,
            process_limit=CATEGORY_SUGGESTION_BACKFILL_LIMIT,
            created_from_email_id=email_id,
        )

    return {
        "category": category_name,
        "category_description": category_description,
        "is_new_category": is_new_category,
        "urgency_score": float(model_output.get("urgency_score", 0.5)),
        "summary": str(model_output.get("summary", "")).strip() or "",
        "named_entities": merged_entities,
        "time_expressions": merged_times,
        "proposed_name": proposed_name,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
