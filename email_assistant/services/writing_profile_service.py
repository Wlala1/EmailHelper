from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from config import BOOTSTRAP_MAX_PROFILE_EMAILS
from datetime import datetime, timezone

from models import Email, UserWritingProfile
from repositories import get_feedback_events_for_user, get_recent_sent_emails, upsert_user_writing_profile

GREETING_PATTERNS = [
    r"^(dear[^\n]{0,80})",
    r"^(hi[^\n]{0,80})",
    r"^(hello[^\n]{0,80})",
]
CLOSING_PATTERNS = [
    r"(best regards[^\n]{0,80})$",
    r"(regards[^\n]{0,80})$",
    r"(thanks[^\n]{0,80})$",
    r"(cheers[^\n]{0,80})$",
]
CTA_PATTERNS = [
    r"please[^\n\.\!\?]{0,120}",
    r"let me know[^\n\.\!\?]{0,120}",
    r"could you[^\n\.\!\?]{0,120}",
    r"can you[^\n\.\!\?]{0,120}",
]


def _plain_text(email: Email) -> str:
    text = email.body_content or email.body_preview or ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _top_matches(patterns: list[str], texts: list[str], *, limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        lowered = text.lower()
        for pattern in patterns:
            for match in re.findall(pattern, lowered, re.IGNORECASE | re.MULTILINE):
                cleaned = re.sub(r"\s+", " ", match).strip(" ,;:")
                if cleaned:
                    counter[cleaned] += 1
    return [value for value, _ in counter.most_common(limit)]


def _extract_signatures(texts: list[str], *, limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        parts = re.split(r"\n|<br\s*/?>", text, flags=re.IGNORECASE)
        tail = [part.strip() for part in parts[-4:] if part.strip()]
        if not tail:
            continue
        signature = " | ".join(tail[-2:])[:120]
        if signature:
            counter[signature] += 1
    return [value for value, _ in counter.most_common(limit)]


def _preferred_language(texts: list[str]) -> str | None:
    if not texts:
        return None
    zh_count = sum(1 for text in texts if re.search(r"[\u4e00-\u9fff]", text))
    return "zh" if zh_count >= max(1, len(texts) // 3) else "en"


def _tone_profile(texts: list[str]) -> str | None:
    if not texts:
        return None
    lowered = "\n".join(texts).lower()
    formal_markers = sum(lowered.count(term) for term in ["dear", "regards", "sincerely", "appreciate"])
    warm_markers = sum(lowered.count(term) for term in ["thanks", "thank you", "glad", "happy"])
    casual_markers = sum(lowered.count(term) for term in ["hi", "hello", "cheers"])
    if formal_markers >= warm_markers and formal_markers >= casual_markers:
        return "formal"
    if warm_markers >= casual_markers:
        return "warm"
    return "casual"


def _avg_length_bucket(texts: list[str]) -> str | None:
    if not texts:
        return None
    avg_len = sum(len(text) for text in texts) / len(texts)
    if avg_len < 250:
        return "short"
    if avg_len < 800:
        return "medium"
    return "long"


def rebuild_user_writing_profile(session: Session, user_id: str) -> dict[str, Any]:
    emails = get_recent_sent_emails(session, user_id, limit=BOOTSTRAP_MAX_PROFILE_EMAILS)
    texts = [_plain_text(email) for email in emails if _plain_text(email)]

    preferred_language = _preferred_language(texts)
    tone_profile = _tone_profile(texts)
    avg_length_bucket = _avg_length_bucket(texts)
    greeting_patterns = _top_matches(GREETING_PATTERNS, texts)
    closing_patterns = _top_matches(CLOSING_PATTERNS, texts)
    signature_blocks = _extract_signatures([email.body_content or email.body_preview or "" for email in emails])
    cta_patterns = _top_matches(CTA_PATTERNS, texts)
    sample_count = len(texts)
    profile_payload = {
        "source_email_ids": [email.email_id for email in emails[:20]],
        "language": preferred_language,
        "tone": tone_profile,
    }

    upsert_user_writing_profile(
        session,
        user_id=user_id,
        preferred_language=preferred_language,
        tone_profile=tone_profile,
        avg_length_bucket=avg_length_bucket,
        greeting_patterns=greeting_patterns,
        closing_patterns=closing_patterns,
        signature_blocks=signature_blocks,
        cta_patterns=cta_patterns,
        sample_count=sample_count,
        profile_payload=profile_payload,
    )
    # Also refresh the preference vector while rebuilding the profile.
    update_preference_vector(session, user_id)
    return {
        "preferred_language": preferred_language,
        "tone_profile": tone_profile,
        "avg_length_bucket": avg_length_bucket,
        "greeting_patterns": greeting_patterns,
        "closing_patterns": closing_patterns,
        "signature_blocks": signature_blocks,
        "cta_patterns": cta_patterns,
        "sample_count": sample_count,
    }


def update_preference_vector(session: Session, user_id: str) -> dict:
    """Recompute and persist the preference_vector on UserWritingProfile.

    Reads all UserFeedbackEvent rows for the user and calculates:
      - tone_accept_rates[tone_key]: accepted / (accepted + rejected + edited)
      - schedule_accept_rate: accepted / (accepted + rejected) for schedule_candidate
      - feedback_count: total events considered

    The result is stored in UserWritingProfile.preference_vector.
    Returns the new preference_vector dict.
    """
    events = get_feedback_events_for_user(session, user_id)
    if not events:
        return {}

    # Tone template feedback.
    tone_counts: dict[str, dict[str, int]] = {}  # {tone_key: {signal: count}}
    # Schedule candidate feedback.
    schedule_accepted = 0
    schedule_rejected = 0
    feedback_count = len(events)

    for event in events:
        signal = event.feedback_signal
        if event.target_type in ("tone_template", "reply_suggestion"):
            tone_key = (event.feedback_metadata or {}).get("tone_key")
            if tone_key:
                bucket = tone_counts.setdefault(tone_key, {"accepted": 0, "rejected": 0, "edited": 0})
                if signal in bucket:
                    bucket[signal] += 1
        elif event.target_type == "schedule_candidate":
            if signal == "accepted":
                schedule_accepted += 1
            elif signal == "rejected":
                schedule_rejected += 1

    tone_accept_rates: dict[str, float] = {}
    for tone_key, counts in tone_counts.items():
        denominator = counts["accepted"] + counts["rejected"] + counts["edited"]
        if denominator > 0:
            tone_accept_rates[tone_key] = round(counts["accepted"] / denominator, 4)

    sched_denom = schedule_accepted + schedule_rejected
    schedule_accept_rate = round(schedule_accepted / sched_denom, 4) if sched_denom > 0 else 0.5

    preference_vector = {
        "tone_accept_rates": tone_accept_rates,
        "schedule_accept_rate": schedule_accept_rate,
        "feedback_count": feedback_count,
    }

    # Persist to the profile row.
    profile = session.get(UserWritingProfile, user_id)
    if profile is not None:
        profile.preference_vector = preference_vector
        profile.preference_vector_updated_at_utc = datetime.now(timezone.utc)
    return preference_vector
