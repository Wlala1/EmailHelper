from __future__ import annotations

import re
from typing import Any, Optional

from agents.classification.common import (
    extract_entities,
    extract_time_expressions,
    normalize_category_name,
    tokenize,
)


def heuristic_sender_role(sender_email: str, sender_name: Optional[str]) -> str:
    sender = (sender_email or "").lower()
    display = (sender_name or "").lower()
    # Derive a short org label from the email domain (eTLD+1 SLD, title-cased)
    org = ""
    if "@" in sender:
        domain = sender.split("@", 1)[1]
        parts = domain.split(".")
        # Handle two-part TLDs like .edu.sg, .co.uk
        if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"edu", "co", "com", "ac", "gov"}:
            sld = parts[-3]
        elif len(parts) >= 2:
            sld = parts[-2]
        else:
            sld = parts[0]
        org = sld.upper() if len(sld) <= 4 else sld.title()

    def _combine(role: str) -> str:
        return f"{org} · {role}" if org else role

    if "prof" in display or "professor" in display:
        return _combine("Professor")
    if "admin" in sender or "office" in sender:
        return _combine("Administrator")
    if "career" in sender or "hr" in sender:
        return _combine("Recruiter")
    if sender.endswith(".edu") or sender.endswith(".edu.sg"):
        return _combine("Student")
    # Use sender display name as role fallback rather than a generic label
    if sender_name and sender_name.strip():
        return _combine(sender_name.strip())
    return _combine("External Contact")


def heuristic_new_category_name(subject: str, text: str) -> str:
    combined = f"{subject}\n{text}".lower()
    if "call for papers" in combined or "cfp" in combined or (
        "submission" in combined and any(k in combined for k in ("deadline", "paper", "conference", "workshop", "journal"))
    ):
        return "Academic Conferences"
    if "canvas" in combined or "assignment" in combined or "quiz" in combined or "grade" in combined:
        return "Canvas Course Updates"
    if "career" in combined or "intern" in combined or "job fair" in combined or "recruit" in combined:
        return "Campus/Faculty Career Opportunities"
    if "teams meeting" in combined or "microsoft teams" in combined or "join meeting" in combined:
        return "Teams Meetings"
    if "event" in combined or "social" in combined or "club" in combined:
        return "Social Events"
    if "invoice" in combined or "payment" in combined or "receipt" in combined:
        return "Billing"

    subject_words = re.findall(r"[A-Za-z0-9]+", subject or "")
    if subject_words:
        return normalize_category_name(" ".join(subject_words[:4]))
    return "General Updates"


def heuristic_new_category_description(name: str, text: str) -> str:
    snippets = text.strip().replace("\n", " ")
    snippets = re.sub(r"\s+", " ", snippets)
    if snippets:
        return f"Emails related to {name}, typically covering: {snippets[:120]}."
    return f"Emails related to {name}."


def best_existing_category(existing_categories: list[dict[str, str]], combined_text: str) -> tuple[str | None, float]:
    content_tokens = tokenize(combined_text)
    if not content_tokens:
        return None, 0.0

    best_name: str | None = None
    best_score = 0.0
    for category in existing_categories:
        category_tokens = tokenize(f"{category['category_name']} {category['category_description']}")
        if not category_tokens:
            continue
        overlap = len(content_tokens & category_tokens)
        score = overlap / max(1, len(category_tokens))
        if score > best_score:
            best_score = score
            best_name = category["category_name"]
    return best_name, best_score


def heuristic_classify(
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
    time_expressions = extract_time_expressions(combined_text)
    if time_expressions:
        urgency = max(urgency, 0.7)

    summary = combined_text.strip().replace("\n", " ")
    summary = summary[:220] + ("..." if len(summary) > 220 else "")

    best_name, best_score = best_existing_category(existing_categories, combined_text)
    if best_name and best_score >= 0.1:
        selected_category_name = best_name
        selected_description = next(
            (item["category_description"] for item in existing_categories if item["category_name"] == best_name),
            f"Emails related to {best_name}.",
        )
        is_new_category = False
    else:
        selected_category_name = heuristic_new_category_name(subject, combined_text)
        selected_description = heuristic_new_category_description(selected_category_name, combined_text)
        is_new_category = True

    return {
        "category_name": normalize_category_name(selected_category_name),
        "category_description": selected_description.strip(),
        "is_new_category": is_new_category,
        "urgency_score": round(min(1.0, urgency), 4),
        "summary": summary or "邮件内容较短，建议查看原文。",
        "sender_role": heuristic_sender_role(sender_email, sender_name),
        "named_entities": extract_entities(combined_text),
        "time_expressions": time_expressions,
    }
