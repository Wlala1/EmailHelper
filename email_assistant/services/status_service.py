from __future__ import annotations

from sqlalchemy.orm import Session

from repositories import (
    get_category_by_name,
    get_current_classifier,
    get_current_reply_suggestion,
    get_current_top_schedule_candidate,
    get_latest_branch_statuses,
    get_latest_reply_draft_write,
)


def build_trace_email_status(session: Session, *, trace_id: str, email_id: str) -> dict[str, object]:
    statuses = get_latest_branch_statuses(
        session,
        trace_id=trace_id,
        email_id=email_id,
        agents=["classifier", "attachment", "relationship_graph", "schedule", "response"],
    )
    classifier = get_current_classifier(session, email_id)
    top_candidate = get_current_top_schedule_candidate(session, email_id)
    latest_draft_write = get_latest_reply_draft_write(session, email_id)
    response = get_current_reply_suggestion(session, email_id)
    category_description = None
    if classifier is not None:
        category = get_category_by_name(session, classifier.user_id, classifier.category)
        category_description = category.category_description if category else None

    return {
        "trace_id": trace_id,
        "email_id": email_id,
        "branch_statuses": statuses,
        "current_classifier": {
            "category": classifier.category,
            "category_description": category_description,
            "urgency_score": classifier.urgency_score,
            "summary": classifier.summary,
            "sender_role": classifier.sender_role,
            "named_entities": classifier.named_entities,
            "time_expressions": classifier.time_expressions,
        }
        if classifier
        else None,
        "current_attachment_status": statuses.get("attachment"),
        "top_schedule_candidate": {
            "candidate_id": top_candidate.candidate_id,
            "title": top_candidate.title,
            "action": top_candidate.action,
            "transaction_id": top_candidate.transaction_id,
            "write_status": top_candidate.write_status,
            "outlook_event_id": top_candidate.outlook_event_id,
        }
        if top_candidate
        else None,
        "current_response": {
            "reply_required": response.reply_required,
            "decision_reason": response.decision_reason,
            "tone_templates": response.tone_templates,
        }
        if response
        else None,
        "current_draft_write": {
            "draft_status": latest_draft_write.draft_status,
            "policy_name": latest_draft_write.policy_name,
            "outlook_draft_id": latest_draft_write.outlook_draft_id,
            "outlook_web_link": latest_draft_write.outlook_web_link,
            "error_message": latest_draft_write.error_message,
        }
        if latest_draft_write
        else None,
    }
