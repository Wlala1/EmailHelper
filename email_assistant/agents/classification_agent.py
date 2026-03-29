from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from agents.input_handler import ParsedAttachment, parse_attachment
from config import MAX_CLASSIFIER_CONTEXT_CHARS, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from models import AttachmentResult, ClassifierResult
from repositories import (
    create_terminal_run,
    get_email,
    get_email_attachments,
    set_non_current_attachment,
    set_non_current_classifier,
)
from schemas import AgentRunStatus, ClassifierOutput, EmailCategory

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None


CLASSIFIER_PROMPT = """You are OUMA classifier.
Classify the email into exactly one category:
- AcademicConferences
- CanvasCourseUpdates
- CampusFacultyCareerOpportunities
- SocialEvents
- TeamsMeetings

Also output:
- urgency_score: number in [0,1]
- summary: concise summary in Chinese
- sender_role: inferred role (Professor/Teammate/Admin/Company/etc.)
- named_entities: list of important entities
- time_expressions: list of time expressions or dates

Return JSON only:
{
  "category": "...",
  "urgency_score": 0.0,
  "summary": "...",
  "sender_role": "...",
  "named_entities": [],
  "time_expressions": []
}
"""


def _dedup_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _heuristic_category(text: str) -> EmailCategory:
    t = text.lower()
    if "call for papers" in t or "cfp" in t or "submission" in t:
        return EmailCategory.academic_conferences
    if "canvas" in t or "assignment" in t or "quiz" in t or "grade" in t:
        return EmailCategory.canvas_course_updates
    if "career" in t or "intern" in t or "job fair" in t or "recruit" in t:
        return EmailCategory.campus_faculty_career_opportunities
    if "teams meeting" in t or "microsoft teams" in t or "join meeting" in t:
        return EmailCategory.teams_meetings
    if "event" in t or "social" in t or "club" in t:
        return EmailCategory.social_events
    return EmailCategory.canvas_course_updates


def _heuristic_sender_role(sender_email: str, sender_name: Optional[str]) -> str:
    sender = (sender_email or "").lower()
    display = (sender_name or "").lower()
    if "prof" in display or "professor" in display:
        return "Professor"
    if "admin" in sender or "office" in sender:
        return "Administration"
    if "career" in sender or "hr" in sender:
        return "CareerService"
    if sender.endswith(".edu") or sender.endswith(".edu.sg"):
        return "Teammate"
    return "ExternalContact"


def _extract_entities(text: str) -> list[str]:
    entities: set[str] = set()
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        entities.add(email)
    for match in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text):
        entities.add(match)
    return sorted(entities)[:30]


def _extract_time_expressions(text: str) -> list[str]:
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{4}/\d{2}/\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    ]
    found: list[str] = []
    for p in patterns:
        found.extend(re.findall(p, text))
    return _dedup_keep_order(found)[:20]


def _heuristic_classify(combined_text: str, sender_email: str, sender_name: Optional[str]) -> ClassifierOutput:
    category = _heuristic_category(combined_text)
    urgency = 0.5
    if re.search(r"\b(urgent|asap|deadline|today|tomorrow)\b", combined_text, re.IGNORECASE):
        urgency = 0.85
    time_expressions = _extract_time_expressions(combined_text)
    if time_expressions:
        urgency = max(urgency, 0.7)
    summary = combined_text.strip().replace("\n", " ")
    summary = summary[:220] + ("..." if len(summary) > 220 else "")
    return ClassifierOutput(
        category=category,
        urgency_score=round(min(1.0, urgency), 4),
        summary=summary or "邮件内容较短，建议查看原文。",
        sender_role=_heuristic_sender_role(sender_email, sender_name),
        named_entities=_extract_entities(combined_text),
        time_expressions=time_expressions,
    )


def _llm_classify(combined_text: str) -> Optional[ClassifierOutput]:
    if _client is None:
        return None
    try:
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": combined_text[:MAX_CLASSIFIER_CONTEXT_CHARS]},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        category_raw = data.get("category", EmailCategory.canvas_course_updates.value)
        valid = {c.value for c in EmailCategory}
        if category_raw not in valid:
            category_raw = EmailCategory.canvas_course_updates.value

        return ClassifierOutput(
            category=EmailCategory(category_raw),
            urgency_score=float(data.get("urgency_score", 0.5)),
            summary=str(data.get("summary", "")).strip() or "未生成摘要",
            sender_role=str(data.get("sender_role", "Unknown")).strip() or "Unknown",
            named_entities=[str(x) for x in data.get("named_entities", [])][:30],
            time_expressions=[str(x) for x in data.get("time_expressions", [])][:20],
        )
    except Exception:
        return None


def _attachment_result_to_dict(parsed: ParsedAttachment) -> dict[str, Any]:
    return {
        "attachment_id": parsed.attachment_id,
        "doc_type": parsed.doc_type,
        "relevance_score": parsed.relevance_score,
        "topics": parsed.topics,
        "named_entities": parsed.named_entities,
        "time_expressions": parsed.time_expressions,
        "extracted_text": parsed.extracted_text,
    }


def _save_attachment_results(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
    parsed_results: list[ParsedAttachment],
) -> None:
    for result in parsed_results:
        set_non_current_attachment(session, result.attachment_id)
        session.add(
            AttachmentResult(
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
        )


def _build_combined_context(email, parsed_attachments: list[ParsedAttachment]) -> str:
    chunks = [
        f"Subject: {email.subject or ''}",
        f"Sender: {email.sender_email or ''} ({email.sender_name or ''})",
        f"Received: {email.received_at_utc.isoformat() if email.received_at_utc else ''}",
        "",
        "Body:",
        email.body_content or email.body_preview or "",
    ]
    if parsed_attachments:
        chunks.append("\nAttachment Extracted Content:")
        for item in parsed_attachments:
            chunks.append(f"\n[Attachment {item.attachment_id}]")
            chunks.append(f"Doc Type: {item.doc_type or 'unknown'}")
            chunks.append(f"Topics: {', '.join(item.topics)}")
            chunks.append(f"Time Expressions: {', '.join(item.time_expressions)}")
            chunks.append(item.extracted_text or "")
    return "\n".join(chunks)


def run_classifier(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, Any]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")

    attachments = get_email_attachments(session, email_id)
    parsed_results: list[ParsedAttachment] = []
    for item in attachments:
        if item.is_inline:
            continue
        if not item.local_path or not os.path.exists(item.local_path):
            continue
        parsed = parse_attachment(
            attachment_id=item.attachment_id,
            name=item.name,
            path=item.local_path,
            content_type=item.content_type,
            sender_email=email.sender_email,
        )
        parsed_results.append(parsed)

    if parsed_results:
        _save_attachment_results(
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

    combined_text = _build_combined_context(email, parsed_results)
    model_output = _llm_classify(combined_text)
    if model_output is None:
        model_output = _heuristic_classify(combined_text, email.sender_email, email.sender_name)

    merged_entities = _dedup_keep_order(
        model_output.named_entities + [e for p in parsed_results for e in p.named_entities]
    )[:30]
    merged_times = _dedup_keep_order(
        model_output.time_expressions + [t for p in parsed_results for t in p.time_expressions]
    )[:20]

    set_non_current_classifier(session, email_id)
    session.add(
        ClassifierResult(
            run_id=run_id,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            category=model_output.category.value,
            urgency_score=model_output.urgency_score,
            summary=model_output.summary,
            sender_role=model_output.sender_role,
            named_entities=merged_entities,
            time_expressions=merged_times,
            is_current=True,
        )
    )

    return {
        "category": model_output.category.value,
        "urgency_score": model_output.urgency_score,
        "summary": model_output.summary,
        "sender_role": model_output.sender_role,
        "named_entities": merged_entities,
        "time_expressions": merged_times,
        "attachment_status": attachment_status,
        "attachment_results": [_attachment_result_to_dict(item) for item in parsed_results],
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
