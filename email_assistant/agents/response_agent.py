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
    set_non_current_reply,
)

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None

SYSTEM_PROMPT = """You are OUMA Response Agent.
Given classifier result, attachment status, relationship snapshot and top schedule candidate,
decide whether a reply is required and produce three tone templates.
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


def _heuristic_response(
    *,
    category: str,
    summary: str,
    attachment_status: str,
    relationship_snapshot: Optional[dict[str, Any]],
    top_schedule_candidate: Optional[dict[str, Any]],
) -> dict[str, Any]:
    relationship_weight = (relationship_snapshot or {}).get("relationship_weight", 0.5)
    action = (top_schedule_candidate or {}).get("action")
    category_l = (category or "").lower()
    is_teams = ("teams" in category_l and "meeting" in category_l) or "meeting" in category_l
    has_schedule_action = action == "create_tentative_event"
    reply_required = bool(is_teams or has_schedule_action or relationship_weight >= 0.7)

    if reply_required:
        reason = "发件人关系强且邮件包含时间/行动信息，建议回复确认。"
    else:
        reason = "当前邮件信息偏通知类，暂不强制回复。"
    if attachment_status == "success":
        reason += " 已参考附件内容。"

    professional = (
        "您好，感谢您的来信。我们已收到相关信息，并会按邮件中的时间安排推进。"
        "如有补充材料请继续发送。"
    )
    casual = "收到啦，谢谢你发来的信息。我这边会按时间安排跟进，有更新我会及时回复。"
    colloquial = "已看到邮件内容，安排上了。后续有新信息直接发我就行。"
    if summary:
        professional += f"\n\n（要点）{summary[:120]}"
        casual += f"\n\n要点：{summary[:120]}"
        colloquial += f"\n\n重点：{summary[:80]}"

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
) -> Optional[dict[str, Any]]:
    if _client is None:
        return None
    try:
        user_payload = {
            "classifier": {"category": category, "summary": summary},
            "attachment_status": attachment_status,
            "relationship_snapshot": relationship_snapshot or {},
            "top_schedule_candidate": top_schedule_candidate or {},
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
    )
    if output is None:
        output = _heuristic_response(
            category=classifier.category,
            summary=classifier.summary,
            attachment_status=attachment_status,
            relationship_snapshot=relationship_snapshot,
            top_schedule_candidate=top_schedule_candidate,
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
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
