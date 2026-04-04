from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from config import MAX_RESPONSE_CONTEXT_CHARS, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from models import ReplySuggestion
from repositories import (
    get_current_classifier,
    get_current_top_schedule_candidate,
    get_relationship_snapshot,
    get_user_writing_profile,
    set_non_current_reply,
)

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None

SYSTEM_PROMPT = """You are OUMA Response Agent.
Given classifier result, attachment status, relationship snapshot, top schedule candidate,
and the user's historical writing profile, decide whether a reply is required and produce three tone templates.
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
    }


def _heuristic_response(
    *,
    category: str,
    summary: str,
    attachment_status: str,
    relationship_snapshot: Optional[dict[str, Any]],
    top_schedule_candidate: Optional[dict[str, Any]],
    writing_profile: Optional[dict[str, Any]],
) -> dict[str, Any]:
    relationship_weight = (relationship_snapshot or {}).get("relationship_weight", 0.5)
    action = (top_schedule_candidate or {}).get("action")
    category_l = (category or "").lower()
    is_teams = ("teams" in category_l and "meeting" in category_l) or "meeting" in category_l
    has_schedule_action = action == "create_tentative_event"
    reply_required = bool(is_teams or has_schedule_action or relationship_weight >= 0.7)
    profile = writing_profile or {}
    preferred_language = (profile.get("preferred_language") or "zh").lower()
    tone_profile = (profile.get("tone_profile") or "formal").lower()
    greeting_patterns = profile.get("greeting_patterns") or []
    closing_patterns = profile.get("closing_patterns") or []
    signature_blocks = profile.get("signature_blocks") or []

    if reply_required:
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

    return {
        "reply_required": reply_required,
        "decision_reason": reason,
        "tone_templates": {
            "professional": professional,
            "casual": casual,
            "colloquial": colloquial,
        },
    }


def _llm_response(
    *,
    category: str,
    summary: str,
    attachment_status: str,
    relationship_snapshot: Optional[dict[str, Any]],
    top_schedule_candidate: Optional[dict[str, Any]],
    writing_profile: Optional[dict[str, Any]],
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

    relationship_snapshot = get_relationship_snapshot(session, email_id)
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
    )
    if output is None:
        output = _heuristic_response(
            category=classifier.category,
            summary=classifier.summary,
            attachment_status=attachment_status,
            relationship_snapshot=relationship_snapshot,
            top_schedule_candidate=top_schedule_candidate,
            writing_profile=writing_profile,
        )

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
        "relationship_snapshot": relationship_snapshot,
        "top_schedule_candidate": top_schedule_candidate,
        "writing_profile": writing_profile,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
