from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agents.input_handler import ParsedAttachment
from config import MAX_ATTACHMENT_CONTEXT_CHARS, MAX_CLASSIFIER_CONTEXT_CHARS

ATTACHMENT_CONTEXT_WRAPPER = "\n\nAttachment Context:\n"
MAX_ATTACHMENT_SUMMARY_SOURCE_CHARS = 6000
MIN_HEURISTIC_ATTACHMENT_SECTION_CHARS = 220

CLASSIFIER_PROMPT = """You are an email classifier with evolving categories.

You receive:
1) Existing categories (name + description)
2) Current email content

Decide:
- whether this email belongs to one of the existing categories
- if not, create a new category with a short clear name and description

Output JSON only:
{
  \"selected_category_name\": \"existing category name if matched\",
  \"is_new_category\": false,
  \"new_category_name\": \"\",
  \"new_category_description\": \"\",
  \"urgency_score\": 0.0,
  \"summary\": \"concise Chinese summary\",
  \"sender_role\": \"Professor/Teammate/Admin/Company/etc.\",
  \"named_entities\": [],
  \"time_expressions\": []
}
"""

ATTACHMENT_SUMMARY_PROMPT = """You compress extracted email attachment content for a downstream classifier.

Return JSON only:
{
  \"attachments\": [
    {
      \"attachment_id\": \"...\",
      \"summary\": \"...\"
    }
  ]
}

Rules:
- Keep the original attachment order.
- Keep each summary concise and factual.
- Preserve document type clues, important dates/deadlines, people/emails/organisations, major content, and action items.
- Do not invent details.
- The combined summary text across all attachments must stay within {max_chars} characters.
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


@dataclass
class ParsedAttachmentRecord:
    name: str
    parsed: ParsedAttachment


@dataclass
class AttachmentAudit:
    raw_chars: int
    included_chars: int
    included_mode: str


@dataclass
class AttachmentContextBundle:
    text: str
    mode: str
    raw_chars: int
    context_chars: int
    audits: dict[str, AttachmentAudit]


def dedup_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def truncate_chars(text: str, limit: int) -> str:
    text = text or ""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def format_values(values: list[str], *, limit: int = 10, default: str = "none") -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return default
    return ", ".join(cleaned[:limit])


def normalize_category_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9\s\-_&/]", " ", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "General Updates"
    if len(name) > 64:
        name = name[:64].strip()
    return name.title()


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower())
    return {word for word in words if word not in STOPWORDS}


def extract_entities(text: str) -> list[str]:
    entities: set[str] = set()
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        entities.add(email)
    for match in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text):
        entities.add(match)
    return sorted(entities)[:30]


def extract_time_expressions(text: str) -> list[str]:
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{4}/\d{2}/\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text))
    return dedup_keep_order(found)[:20]


def build_email_context(email: Any, budget: int) -> str:
    if budget <= 0:
        return ""
    prefix = "\n".join(
        [
            f"Subject: {email.subject or ''}",
            f"Sender: {email.sender_email or ''} ({email.sender_name or ''})",
            f"Received: {email.received_at_utc.isoformat() if email.received_at_utc else ''}",
            "",
            "Body:",
        ]
    )
    base = f"{prefix}\n"
    body = email.body_content or email.body_preview or ""
    if len(base) >= budget:
        return truncate_chars(prefix, budget)
    return f"{base}{truncate_chars(body, budget - len(base))}".rstrip()


def build_combined_context(email: Any, attachment_bundle: AttachmentContextBundle) -> str:
    if attachment_bundle.context_chars <= 0:
        return build_email_context(email, MAX_CLASSIFIER_CONTEXT_CHARS)
    email_budget = max(0, MAX_CLASSIFIER_CONTEXT_CHARS - MAX_ATTACHMENT_CONTEXT_CHARS - len(ATTACHMENT_CONTEXT_WRAPPER))
    email_context = build_email_context(email, email_budget)
    return f"{email_context}{ATTACHMENT_CONTEXT_WRAPPER}{attachment_bundle.text}"
