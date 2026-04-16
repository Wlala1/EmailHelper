from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agents.classification.attachment_context import attachment_result_to_dict, build_attachment_context
from agents.classification.common import ParsedAttachmentRecord, build_combined_context, dedup_keep_order, normalize_category_name
from agents.classification.heuristics import heuristic_classify, heuristic_new_category_description
from agents.classification.llm import llm_classify
from agents.classification.persistence import save_attachment_results
from agents.input_handler import parse_attachment
from repository.common import local_path_exists
from repositories import (
    create_category_definition,
    create_terminal_run,
    get_category_definitions,
    get_email,
    get_email_attachments,
    set_non_current_classifier,
)
from schemas import AgentRunStatus
from models import ClassifierResult


def run_classifier(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
    category_catalog_override: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")

    attachments = get_email_attachments(session, email_id)
    parsed_results: list[ParsedAttachmentRecord] = []
    for item in attachments:
        if item.is_inline or not local_path_exists(item.local_path):
            continue
        parsed = parse_attachment(
            attachment_id=item.attachment_id,
            name=item.name,
            path=item.local_path,
            content_type=item.content_type,
            sender_email=email.sender_email,
        )
        parsed_results.append(ParsedAttachmentRecord(name=item.name, parsed=parsed))

    if parsed_results:
        save_attachment_results(
            session,
            trace_id=trace_id,
            run_id=run_id,
            email_id=email_id,
            user_id=user_id,
            parsed_results=parsed_results,
        )
        create_terminal_run(
            session,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            agent_name="attachment",
            status=AgentRunStatus.success,
            input_payload={"email_id": email_id},
            output_payload={"results_count": len(parsed_results)},
            upstream_run_id=run_id,
        )
        attachment_status = AgentRunStatus.success.value
    else:
        create_terminal_run(
            session,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            agent_name="attachment",
            status=AgentRunStatus.skipped,
            input_payload={"email_id": email_id},
            output_payload={"status": "skipped", "reason": "no_attachments"},
            upstream_run_id=run_id,
        )
        attachment_status = AgentRunStatus.skipped.value

    existing_categories = category_catalog_override or [
        {"category_name": item.category_name, "category_description": item.category_description}
        for item in get_category_definitions(session, user_id)
    ]

    attachment_context = build_attachment_context(parsed_results)
    combined_text = build_combined_context(email, attachment_context)
    model_output = llm_classify(
        combined_text=combined_text,
        existing_categories=existing_categories,
        subject=email.subject or "",
    )
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
    if category_name in existing_lookup:
        category_description = existing_lookup[category_name]
        is_new_category = False
    else:
        if not category_description:
            category_description = heuristic_new_category_description(category_name, combined_text)
        create_category_definition(
            session,
            user_id=user_id,
            category_name=category_name,
            category_description=category_description,
            created_from_email_id=email_id,
        )
        is_new_category = True

    merged_entities = dedup_keep_order(
        [str(item) for item in model_output.get("named_entities", [])]
        + [entity for record in parsed_results for entity in record.parsed.named_entities]
    )[:30]
    merged_times = dedup_keep_order(
        [str(item) for item in model_output.get("time_expressions", [])]
        + [value for record in parsed_results for value in record.parsed.time_expressions]
    )[:20]

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
            sender_role=str(model_output.get("sender_role", "Unknown")).strip() or "Unknown",
            named_entities=merged_entities,
            time_expressions=merged_times,
            is_current=True,
        )
    )

    category_catalog = get_category_definitions(session, user_id)
    return {
        "category": category_name,
        "category_description": category_description,
        "is_new_category": is_new_category,
        "urgency_score": float(model_output.get("urgency_score", 0.5)),
        "summary": str(model_output.get("summary", "")).strip() or "",
        "sender_role": str(model_output.get("sender_role", "Unknown")).strip() or "Unknown",
        "named_entities": merged_entities,
        "time_expressions": merged_times,
        "attachment_status": attachment_status,
        "attachment_context_mode": attachment_context.mode,
        "attachment_raw_chars": attachment_context.raw_chars,
        "attachment_context_chars": attachment_context.context_chars,
        "attachment_results": [
            attachment_result_to_dict(record, attachment_context.audits.get(record.parsed.attachment_id))
            for record in parsed_results
        ],
        "category_catalog": [
            {"category_name": item.category_name, "category_description": item.category_description}
            for item in category_catalog
        ],
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
