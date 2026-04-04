from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from config import MAX_RESPONSE_CONTEXT_CHARS, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from models import ReplySuggestion
from repositories import (
    get_current_classifier,
    get_current_top_schedule_candidate,
    get_email,
    get_relationship_snapshot,
    get_user_writing_profile,
    set_non_current_reply,
)
from services.neo4j_service import get_person_context

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None

SYSTEM_PROMPT = """You are OUMA Response Agent.
Given classifier result, attachment status, relationship snapshot, top schedule candidate,
the user's historical writing profile, and the sender's identity tier (1=authority/professor,
2=professional/external, 3=peer/teammate), decide whether a reply is required and produce
three tone templates.

Identity tier guidance:
  Tier 1 (professor/advisor/director): always reply_required=true, use professional tone.
  Tier 2 (recruiter/manager/client): reply if relationship is warm or meeting involved.
  Tier 3 (teammate/peer/student): reply if action required or relationship strong.

Return JSON only:
{
  "reply_required": true/false,
  "decision_reason": "...",
  "tone_templates": {
    "professional": "...",
    "casual": "...",
    "colloquial": "..."
  }
}
"""

# Sender identity tier mapping: keyword → tier integer
IDENTITY_TIER_MAP: dict[str, int] = {
    # Tier 1 — high authority / mentorship
    "professor": 1, "advisor": 1, "supervisor": 1, "pi ": 1, "director": 1,
    "dean": 1, "principal": 1, "head of": 1, "chief": 1,
    # Tier 2 — professional / external
    "recruiter": 2, "hr": 2, "manager": 2, "partner": 2, "client": 2,
    "employer": 2, "hiring": 2, "talent": 2, "vendor": 2, "consultant": 2,
    # Tier 3 — peer / internal (default)
    "teammate": 3, "colleague": 3, "intern": 3, "student": 3, "recipient": 3,
    "peer": 3, "classmate": 3, "ta": 3, "assistant": 3,
}


def _sender_tier(person_role: Optional[str]) -> int:
    """Map a person_role string to a tier integer (1=authority, 2=professional, 3=peer)."""
    if not person_role:
        return 3
    role_lower = person_role.lower()
    for keyword, tier in IDENTITY_TIER_MAP.items():
        if keyword in role_lower:
            return tier
    return 3


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    if profile is None:
        return {}
    return {
        "preferred_language": getattr(profile, "preferred_language", None),
        "tone_profile": getattr(profile, "tone_profile", None),
        "avg_length_bucket": getattr(profile, "avg_length_bucket", None),
        "greeting_patterns": list(getattr(profile, "greeting_patterns", []) or []),
        "closing_patterns": list(getattr(profile, "closing_patterns", []) or []),
        "signature_blocks": list(getattr(profile, "signature_blocks", []) or []),
        "cta_patterns": list(getattr(profile, "cta_patterns", []) or []),
        "sample_count": int(getattr(profile, "sample_count", 0) or 0),
        "preference_vector": dict(getattr(profile, "preference_vector", None) or {}),
    }


def _heuristic_response(
    *,
    category: str,
    summary: str,
    attachment_status: str,
    relationship_snapshot: Optional[dict[str, Any]],
    top_schedule_candidate: Optional[dict[str, Any]],
    writing_profile: Optional[dict[str, Any]],
    identity_tier: int = 3,
    shared_events: Optional[list[str]] = None,
) -> dict[str, Any]:
    relationship_weight = (relationship_snapshot or {}).get("relationship_weight", 0.5)
    action = (top_schedule_candidate or {}).get("action")
    category_l = (category or "").lower()
    is_teams = ("teams" in category_l and "meeting" in category_l) or "meeting" in category_l
    has_schedule_action = action == "create_tentative_event"
    # If the sender has been involved in previous shared events with the user,
    # that signals an ongoing relationship → lean toward replying.
    has_shared_events = bool(shared_events)

    # Identity-tier-aware reply decision.
    if identity_tier == 1:
        # Always reply to authority contacts (professors, advisors, directors).
        reply_required = True
    else:
        reply_required = bool(is_teams or has_schedule_action or relationship_weight >= 0.7 or has_shared_events)

    profile = writing_profile or {}
    preferred_language = (profile.get("preferred_language") or "zh").lower()
    tone_profile = (profile.get("tone_profile") or "formal").lower()
    greeting_patterns = profile.get("greeting_patterns") or []
    closing_patterns = profile.get("closing_patterns") or []
    signature_blocks = profile.get("signature_blocks") or []

    # Apply preference vector: pick the tone key with the highest acceptance rate
    # within the set of tones allowed for this identity tier.
    pref_vector = profile.get("preference_vector") or {}
    tone_accept_rates: dict[str, float] = pref_vector.get("tone_accept_rates", {})
    tier_allowed_tones: dict[int, list[str]] = {
        1: ["professional"],
        2: ["professional", "casual"],
        3: ["professional", "casual", "colloquial"],
    }
    allowed = tier_allowed_tones.get(identity_tier, ["professional", "casual", "colloquial"])
    if tone_accept_rates:
        from config import USE_PREFERENCE_VECTOR
        if USE_PREFERENCE_VECTOR:
            preferred_tone_key = max(allowed, key=lambda t: tone_accept_rates.get(t, 0.5))
        else:
            preferred_tone_key = allowed[0]
    else:
        preferred_tone_key = allowed[0]

    if identity_tier == 1:
        reason = "发件人为高权重联系人（教授/导师/主管），建议正式回复。"
    elif reply_required and has_shared_events:
        reason = "发件人有历史共同事件记录，建议保持回复。"
    elif reply_required:
        reason = "发件人关系强且邮件包含时间/行动信息，建议回复确认。"
    else:
        reason = "当前邮件信息偏通知类，暂不强制回复。"
    if attachment_status == "success":
        reason += " 已参考附件内容。"

    greeting = greeting_patterns[0] if greeting_patterns else ("您好" if preferred_language.startswith("zh") else "Hello")
    closing = closing_patterns[0] if closing_patterns else ("此致" if preferred_language.startswith("zh") else "Best regards")
    signature = signature_blocks[0].replace(" | ", "\n") if signature_blocks else ""

    if preferred_language.startswith("en"):
        professional = (
            f"{greeting},\n\n"
            "Thank you for your email. I have reviewed the details and will follow up based on the requested timeline. "
            "Please feel free to send any additional materials if needed.\n\n"
            f"{closing}"
        )
        casual = (
            f"{greeting},\n\n"
            "Thanks for sharing this. I have noted the details and will follow up on the timeline from my side.\n\n"
            f"{closing}"
        )
        colloquial = (
            f"{greeting},\n\n"
            "Got it. I have the key points and will keep things moving.\n\n"
            f"{closing}"
        )
        if tone_profile == "casual":
            casual = casual.replace("Thanks for sharing this.", "Thanks for sending this over.")
            colloquial = colloquial.replace("Got it.", "Looks good, got it.")
        elif tone_profile == "warm":
            professional = professional.replace("Thank you for your email.", "Thank you for reaching out.")
    else:
        professional = (
            f"{greeting}，\n\n"
            "感谢来信。我已查看相关信息，会按邮件中的时间安排推进；如有补充材料，也欢迎继续发送。\n\n"
            f"{closing}"
        )
        casual = (
            f"{greeting}，\n\n"
            "收到，谢谢你发来的信息。我这边会按时间安排跟进，有更新会及时回复。\n\n"
            f"{closing}"
        )
        colloquial = (
            f"{greeting}，\n\n"
            "我已经看到重点了，后面会继续跟进安排。\n\n"
            f"{closing}"
        )
        if tone_profile == "casual":
            casual = casual.replace("收到，谢谢你发来的信息。", "收到啦，谢谢你发来的信息。")
            colloquial = colloquial.replace("我已经看到重点了，后面会继续跟进安排。", "重点都看到了，后面我来继续推进。")
        elif tone_profile == "warm":
            professional = professional.replace("感谢来信。", "感谢你的来信。")

    if summary:
        professional += f"\n\n（要点）{summary[:120]}"
        casual += f"\n\n要点：{summary[:120]}"
        colloquial += f"\n\n重点：{summary[:80]}"
    if signature:
        professional += f"\n\n{signature}"
        casual += f"\n\n{signature}"
        colloquial += f"\n\n{signature}"

    tone_templates = {
        "professional": professional,
        "casual": casual,
        "colloquial": colloquial,
    }

    return {
        "reply_required": reply_required,
        "decision_reason": reason,
        "tone_templates": tone_templates,
        "preferred_tone_key": preferred_tone_key,
        "identity_tier": identity_tier,
    }


def _llm_response(
    *,
    category: str,
    summary: str,
    attachment_status: str,
    relationship_snapshot: Optional[dict[str, Any]],
    top_schedule_candidate: Optional[dict[str, Any]],
    writing_profile: Optional[dict[str, Any]],
    identity_tier: int = 3,
    shared_org_members: Optional[list[str]] = None,
    shared_events: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    if _client is None:
        return None
    try:
        user_payload = {
            "classifier": {"category": category, "summary": summary},
            "attachment_status": attachment_status,
            "relationship_snapshot": relationship_snapshot or {},
            "top_schedule_candidate": top_schedule_candidate or {},
            "writing_profile": writing_profile or {},
            "identity_tier": identity_tier,
            "shared_org_members": shared_org_members or [],
            "shared_events_count": len(shared_events or []),
        }
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)[:MAX_RESPONSE_CONTEXT_CHARS]},
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        if "tone_templates" not in data or not isinstance(data["tone_templates"], dict):
            return None
        data["reply_required"] = bool(data.get("reply_required", False))
        data["decision_reason"] = str(data.get("decision_reason", ""))
        for k in ("professional", "casual", "colloquial"):
            data["tone_templates"].setdefault(k, "")
        return data
    except Exception:
        return None


def run_response(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
    attachment_status: str,
) -> dict[str, Any]:
    classifier = get_current_classifier(session, email_id)
    if classifier is None:
        raise ValueError("classifier current result missing")

    email = get_email(session, email_id)
    sender_email = email.sender_email if email else None
    relationship_snapshot: Optional[dict[str, Any]] = None
    shared_org_members: list[str] = []
    shared_events: list[str] = []
    if sender_email:
        try:
            neo4j_context = get_person_context(user_id=user_id, person_email=sender_email)
            if neo4j_context:
                relationship_snapshot = {
                    k: v for k, v in neo4j_context.items() if v is not None
                }
                shared_org_members = neo4j_context.get("shared_org_members") or []
                shared_events = neo4j_context.get("shared_events") or []
        except Exception as exc:
            logger.debug("Neo4j person context unavailable: %s", exc)

    # Fill any missing values from the SQL fallback snapshot.
    sql_snapshot = get_relationship_snapshot(session, email_id)
    if sql_snapshot:
        relationship_snapshot = {
            **sql_snapshot,
            **(relationship_snapshot or {}),
        }

    # Determine sender identity tier.
    person_role = (relationship_snapshot or {}).get("sender_role") or (relationship_snapshot or {}).get("person_role")
    identity_tier = _sender_tier(person_role)

    top_candidate = get_current_top_schedule_candidate(session, email_id)
    writing_profile = _profile_to_dict(get_user_writing_profile(session, user_id))
    top_schedule_candidate = None
    if top_candidate:
        top_schedule_candidate = {
            "candidate_id": top_candidate.candidate_id,
            "title": top_candidate.title,
            "action": top_candidate.action,
        }

    output = _llm_response(
        category=classifier.category,
        summary=classifier.summary,
        attachment_status=attachment_status,
        relationship_snapshot=relationship_snapshot,
        top_schedule_candidate=top_schedule_candidate,
        writing_profile=writing_profile,
        identity_tier=identity_tier,
        shared_org_members=shared_org_members,
        shared_events=shared_events,
    )
    if output is None:
        output = _heuristic_response(
            category=classifier.category,
            summary=classifier.summary,
            attachment_status=attachment_status,
            relationship_snapshot=relationship_snapshot,
            top_schedule_candidate=top_schedule_candidate,
            writing_profile=writing_profile,
            identity_tier=identity_tier,
            shared_events=shared_events,
        )

    # Always force reply_required=True for Tier 1 senders, even if LLM disagrees.
    if identity_tier == 1:
        output["reply_required"] = True

    set_non_current_reply(session, email_id)
    session.add(
        ReplySuggestion(
            run_id=run_id,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            reply_required=output["reply_required"],
            decision_reason=output["decision_reason"],
            tone_templates=output["tone_templates"],
            is_current=True,
        )
    )

    return {
        "reply_required": output["reply_required"],
        "decision_reason": output["decision_reason"],
        "tone_templates": output["tone_templates"],
        "identity_tier": identity_tier,
        "preferred_tone_key": output.get("preferred_tone_key", "professional"),
        "shared_org_members": shared_org_members,
        "shared_events": shared_events,
        "relationship_snapshot": relationship_snapshot,
        "top_schedule_candidate": top_schedule_candidate,
        "writing_profile": writing_profile,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
