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
  \"named_entities\": [],
  \"time_expressions\": []
}
"""

CLASSIFIER_TOOL_PROMPT = """You are an email classifier. Use the tools to actively explore \
existing categories before making a decision.

Workflow (follow in order):
1. Call search_categories with 2-3 keywords from the email subject/body to find relevant categories.
2. If a result looks promising, call get_category_details to read its full description.
3. If an existing category fits well, use it. If none fit, create a new one.
4. Call finalize_classification ONCE as your last action with ALL required fields.

Rules:
- Prefer reusing an existing category over creating a new one.
- Only create a new category if no existing one matches the email's topic after searching.
- category_name must be title-case, ≤ 64 chars, letters/numbers/spaces/hyphens only.
- summary must be a concise Chinese summary (2-3 sentences).
- urgency_score: 0.9+ for deadlines/action-required, 0.5 for informational, 0.2 for newsletters.
- named_entities: people, organisations, emails found in the email (max 10).
- time_expressions: dates/times found in the email in their original form (max 10).
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
    # articles / determiners
    "the", "this", "that", "these", "those", "any", "all", "both", "each",
    # pronouns
    "you", "your", "our", "their", "they", "them", "its", "his", "her",
    "we", "who", "whom", "which", "what",
    # prepositions / conjunctions
    "with", "from", "into", "onto", "upon", "about", "above", "below",
    "before", "after", "between", "through", "during", "without",
    "and", "but", "not", "nor", "yet", "for", "than", "then", "also",
    # common auxiliaries / modals
    "have", "has", "had", "having", "been", "being", "are", "were", "was",
    "will", "would", "could", "should", "shall", "may", "might", "must",
    "can", "does", "did", "doing",
    # super-generic verbs
    "get", "got", "let", "set", "put", "use", "used", "make", "made",
    "take", "took", "give", "gave", "see", "look", "come", "came", "know",
    "want", "need", "like", "just", "now", "new", "add", "include",
    # email-specific filler
    "please", "subject", "email", "thanks", "dear", "team", "regards",
    "hello", "there", "best", "hope", "write", "reply", "send", "sent",
    "attach", "attached", "forward", "message", "receipt", "confirm",
    # generic adjectives / adverbs
    "very", "more", "most", "some", "such", "only", "other", "same",
    "well", "also", "here", "where", "when", "how", "why", "much",
    # layout / CSS words that leak from HTML body_content
    "vertical", "focus", "allow", "become", "optimization", "display",
    "margin", "padding", "border", "color", "background", "font",
    "width", "height", "align", "content", "block", "inline", "none",
    "auto", "absolute", "relative", "flex", "grid", "style", "class",
    "table", "tbody", "thead", "span", "div", "html", "body", "head",
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
    normalized = name.title()
    proposal_aliases = {
        "Course Updates": "Canvas Course Updates",
        "Canvas Updates": "Canvas Course Updates",
        "Canvas Course Update": "Canvas Course Updates",
        "Career Opportunities": "Campus/Faculty Career Opportunities",
        "Campus Faculty Career Opportunities": "Campus/Faculty Career Opportunities",
    }
    return proposal_aliases.get(normalized, normalized)


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower())
    return {word for word in words if word not in STOPWORDS}


def extract_entities(text: str) -> list[str]:
    entities: set[str] = set()
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        entities.add(email)
    for match in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text):
        entities.add(match)
    return sorted(entities)[:10]


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
    return dedup_keep_order(found)[:10]


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
