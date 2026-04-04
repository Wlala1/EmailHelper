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
    create_category_definition,
    create_terminal_run,
    get_category_definitions,
    get_email,
    get_email_attachments,
    set_non_current_attachment,
    set_non_current_classifier,
)
from schemas import AgentRunStatus

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None

CLASSIFIER_PROMPT = """You are an email classifier with evolving categories.

You receive:
1) Existing categories (name + description)
2) Current email content

Decide:
- whether this email belongs to one of the existing categories
- if not, create a new category with a short clear name and description

Output JSON only:
{
  "selected_category_name": "existing category name if matched",
  "is_new_category": false,
  "new_category_name": "",
  "new_category_description": "",
  "urgency_score": 0.0,
  "summary": "concise Chinese summary",
  "sender_role": "Professor/Teammate/Admin/Company/etc.",
  "named_entities": [],
  "time_expressions": []
}
"""

STOPWORDS = {
    "this",
    "that",
    "with",
    "from",
    "your",
    "have",
    "will",
    "please",
    "subject",
    "email",
    "about",
    "thanks",
    "dear",
    "team",
    "regards",
    "hello",
    "there",
}


def _dedup_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _normalize_category_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9\s\-_&/]", " ", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "General Updates"
    if len(name) > 64:
        name = name[:64].strip()
    return name.title()


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


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


def _heuristic_new_category_name(subject: str, text: str) -> str:
    t = f"{subject}\n{text}".lower()
    if "call for papers" in t or "cfp" in t or "submission" in t:
        return "Academic Conferences"
    if "canvas" in t or "assignment" in t or "quiz" in t or "grade" in t:
        return "Course Updates"
    if "career" in t or "intern" in t or "job fair" in t or "recruit" in t:
        return "Career Opportunities"
    if "teams meeting" in t or "microsoft teams" in t or "join meeting" in t:
        return "Teams Meetings"
    if "event" in t or "social" in t or "club" in t:
        return "Social Events"
    if "invoice" in t or "payment" in t or "receipt" in t:
        return "Billing"

    subject_words = re.findall(r"[A-Za-z0-9]+", subject or "")
    if subject_words:
        return _normalize_category_name(" ".join(subject_words[:4]))
    return "General Updates"


def _heuristic_new_category_description(name: str, text: str) -> str:
    snippets = text.strip().replace("\n", " ")
    snippets = re.sub(r"\s+", " ", snippets)
    if snippets:
        return f"Emails related to {name}, typically covering: {snippets[:120]}."
    return f"Emails related to {name}."


def _best_existing_category(
    existing_categories: list[dict[str, str]],
    combined_text: str,
) -> tuple[Optional[str], float]:
    content_tokens = _tokenize(combined_text)
    if not content_tokens:
        return None, 0.0

    best_name: Optional[str] = None
    best_score = 0.0
    for cat in existing_categories:
        cat_tokens = _tokenize(f"{cat['category_name']} {cat['category_description']}")
        if not cat_tokens:
            continue
        overlap = len(content_tokens & cat_tokens)
        score = overlap / max(1, len(cat_tokens))
        if score > best_score:
            best_score = score
            best_name = cat["category_name"]
    return best_name, best_score


def _heuristic_classify(
    *,
    combined_text: str,
    sender_email: str,
    sender_name: Optional[str],
    subject: str,
    existing_categories: list[dict[str, str]],
) -> dict[str, Any]:
    urgency = 0.5
    if re.search(r"\b(urgent|asap|deadline|today|tomorrow)\b", combined_text, re.IGNORECASE):
        urgency = 0.85
    time_expressions = _extract_time_expressions(combined_text)
    if time_expressions:
        urgency = max(urgency, 0.7)

    summary = combined_text.strip().replace("\n", " ")
    summary = summary[:220] + ("..." if len(summary) > 220 else "")

    best_name, best_score = _best_existing_category(existing_categories, combined_text)
    if best_name and best_score >= 0.1:
        selected_category_name = best_name
        selected_description = next(
            (x["category_description"] for x in existing_categories if x["category_name"] == best_name),
            f"Emails related to {best_name}.",
        )
        is_new_category = False
    else:
        selected_category_name = _heuristic_new_category_name(subject, combined_text)
        selected_description = _heuristic_new_category_description(selected_category_name, combined_text)
        is_new_category = True

    return {
        "category_name": _normalize_category_name(selected_category_name),
        "category_description": selected_description.strip(),
        "is_new_category": is_new_category,
        "urgency_score": round(min(1.0, urgency), 4),
        "summary": summary or "邮件内容较短，建议查看原文。",
        "sender_role": _heuristic_sender_role(sender_email, sender_name),
        "named_entities": _extract_entities(combined_text),
        "time_expressions": time_expressions,
    }


def _llm_classify(
    *,
    combined_text: str,
    existing_categories: list[dict[str, str]],
    subject: str,
) -> Optional[dict[str, Any]]:
    if _client is None:
        return None
    try:
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "existing_categories": existing_categories,
                            "subject": subject,
                            "content": combined_text[:MAX_CLASSIFIER_CONTEXT_CHARS],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        existing_names = {x["category_name"] for x in existing_categories}
        is_new_category = bool(data.get("is_new_category", False))

        selected_category_name = _normalize_category_name(str(data.get("selected_category_name", "")).strip())
        new_category_name = _normalize_category_name(str(data.get("new_category_name", "")).strip())
        new_category_description = str(data.get("new_category_description", "")).strip()

        if is_new_category:
            category_name = new_category_name or selected_category_name
            category_description = new_category_description or f"Emails related to {category_name}."
        else:
            if selected_category_name not in existing_names and existing_names:
                category_name = next(iter(existing_names))
            elif not existing_names:
                is_new_category = True
                category_name = new_category_name or selected_category_name or _heuristic_new_category_name(subject, combined_text)
            else:
                category_name = selected_category_name

            if is_new_category:
                category_description = new_category_description or f"Emails related to {category_name}."
            else:
                category_description = next(
                    (x["category_description"] for x in existing_categories if x["category_name"] == category_name),
                    f"Emails related to {category_name}.",
                )

        return {
            "category_name": _normalize_category_name(category_name),
            "category_description": category_description.strip(),
            "is_new_category": is_new_category,
            "urgency_score": float(data.get("urgency_score", 0.5)),
            "summary": str(data.get("summary", "")).strip() or "未生成摘要",
            "sender_role": str(data.get("sender_role", "Unknown")).strip() or "Unknown",
            "named_entities": [str(x) for x in data.get("named_entities", [])][:30],
            "time_expressions": [str(x) for x in data.get("time_expressions", [])][:20],
        }
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

    existing_categories = [
        {
            "category_name": item.category_name,
            "category_description": item.category_description,
        }
        for item in get_category_definitions(session, user_id)
    ]

    combined_text = _build_combined_context(email, parsed_results)
    model_output = _llm_classify(
        combined_text=combined_text,
        existing_categories=existing_categories,
        subject=email.subject or "",
    )
    if model_output is None:
        model_output = _heuristic_classify(
            combined_text=combined_text,
            sender_email=email.sender_email,
            sender_name=email.sender_name,
            subject=email.subject or "",
            existing_categories=existing_categories,
        )

    category_name = _normalize_category_name(model_output["category_name"])
    category_description = (model_output.get("category_description") or "").strip()
    existing_lookup = {x["category_name"]: x["category_description"] for x in existing_categories}

    is_new_category = bool(model_output.get("is_new_category", False))
    if category_name in existing_lookup:
        category_description = existing_lookup[category_name]
        is_new_category = False
    else:
        if not category_description:
            category_description = _heuristic_new_category_description(category_name, combined_text)
        create_category_definition(
            session,
            user_id=user_id,
            category_name=category_name,
            category_description=category_description,
            created_from_email_id=email_id,
        )
        is_new_category = True

    merged_entities = _dedup_keep_order(
        [str(x) for x in model_output.get("named_entities", [])] + [e for p in parsed_results for e in p.named_entities]
    )[:30]
    merged_times = _dedup_keep_order(
        [str(x) for x in model_output.get("time_expressions", [])] + [t for p in parsed_results for t in p.time_expressions]
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
            summary=str(model_output.get("summary", "")).strip() or "未生成摘要",
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
        "summary": str(model_output.get("summary", "")).strip() or "未生成摘要",
        "sender_role": str(model_output.get("sender_role", "Unknown")).strip() or "Unknown",
        "named_entities": merged_entities,
        "time_expressions": merged_times,
        "attachment_status": attachment_status,
        "attachment_results": [_attachment_result_to_dict(item) for item in parsed_results],
        "category_catalog": [
            {
                "category_name": item.category_name,
                "category_description": item.category_description,
            }
            for item in category_catalog
        ],
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
