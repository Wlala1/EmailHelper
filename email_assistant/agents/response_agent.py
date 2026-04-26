from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from typing_extensions import TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import MAX_RESPONSE_CONTEXT_CHARS, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from models import ReplySuggestion
from repositories import (
    get_conversation_thread,
    get_current_classifier,
    get_current_top_schedule_candidate,
    get_email,
    get_relationship_snapshot,
    get_user_writing_profile,
    set_non_current_reply,
)
from services.neo4j_service import get_person_context

logger = logging.getLogger(__name__)


class DecisionOutput(BaseModel):
    reply_required: bool = Field(default=False)
    decision_reason: str = Field(default="")


class ResponseOutput(BaseModel):
    tone_templates: dict[str, str] = Field(default_factory=dict)


class ReviewState(TypedDict):
    # Immutable context — email + enrichment signals
    subject: str
    body_preview: str
    category: str
    summary: str
    attachment_status: str
    relationship_snapshot: dict
    top_schedule_candidate: Optional[dict]
    writing_profile: dict
    identity_tier: int
    shared_org_members: list
    shared_events: list
    conversation_context: str
    sender_name: str
    sender_email: str
    # Mutable
    reply_required: bool
    decision_reason: str
    draft: Optional[dict]


def _make_response_llm() -> Optional[ChatOpenAI]:
    if not OPENAI_API_KEY:
        return None
    kwargs: dict[str, Any] = dict(api_key=OPENAI_API_KEY, model=OPENAI_MODEL, temperature=0.4)
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    return ChatOpenAI(**kwargs)


_response_llm = _make_response_llm()
_decision_chain = _response_llm.with_structured_output(DecisionOutput, method="function_calling") if _response_llm else None
_draft_llm = _response_llm  # raw LLM — draft node parses JSON manually

DECISION_PROMPT = """You are a reply-necessity evaluator for an email assistant.
Read the email context and decide whether a reply from the user is genuinely needed.

Guidelines:
- Reply IS needed when: a real person asks a question, requests an action, invites attendance,
  shares something that warrants acknowledgment, or is following up on a prior conversation.
- Reply is NOT needed when: the email is an automated notification, newsletter, mass announcement,
  system alert, promotional offer, event broadcast, or any message where no personal response
  is expected.
- Ignore identity tier rules — base your decision purely on what the email actually says
  and whether a human response makes sense.

Return JSON only:
{
  "reply_required": true/false,
  "decision_reason": "one concise sentence explaining why"
}
"""

DRAFT_PROMPT = """You are OUMA Response Agent.
Given the email context, produce three tone variants of a reply.
The reply has already been determined to be necessary — your job is only to draft it well.

Use the writing profile (greeting/closing patterns, preferred language, tone) to match
the user's natural style. Use conversation history to reference prior context naturally
and avoid repeating what has already been discussed.

Identity tier guidance for tone:
  Tier 1 (professor/advisor/director): formal and deferential.
  Tier 2 (recruiter/manager/client): professional and courteous.
  Tier 3 (teammate/peer/student): can be casual or colloquial.

Return JSON only:
{
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
    # Tier 0 — automated / broadcast (never reply)
    "system": 0, "broadcast": 0, "newsletter": 0, "noreply": 0, "no-reply": 0,
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


def _retrieve_conversation_context(
    session: Session,
    email: Any,
    *,
    max_emails: int = 5,
    max_chars_per_email: int = 400,
) -> str:
    """Retrieve and format prior emails in the same conversation thread.

    Uses conversation_id (Microsoft Graph thread ID) so only emails from
    the exact same reply chain are returned — not arbitrary emails from
    the same sender. Returns an empty string when no thread history exists.
    """
    if not email or not email.conversation_id:
        return ""

    thread_emails = get_conversation_thread(
        session,
        conversation_id=email.conversation_id,
        exclude_email_id=email.email_id,
        limit=max_emails,
    )
    if not thread_emails:
        return ""

    parts: list[str] = []
    for e in thread_emails:
        date_str = (
            e.received_at_utc.strftime("%Y-%m-%d %H:%M UTC")
            if e.received_at_utc else "unknown date"
        )
        # Use direction to label who sent this message.
        speaker = "You" if e.direction == "outbound" else (e.sender_name or e.sender_email or "Sender")
        # Prefer body_preview (already plain-text); fall back to body_content truncated.
        body = (e.body_preview or e.body_content or "").strip()
        body = body[:max_chars_per_email]
        parts.append(
            f"[{date_str} · {speaker}]\n"
            f"Subject: {e.subject or '(no subject)'}\n"
            f"{body}"
        )

    return "--- Conversation History (same thread) ---\n\n" + "\n\n".join(parts)


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


def _decision_node(state: ReviewState) -> dict:
    """LangGraph node: decide whether a reply is needed based on email content."""
    if _decision_chain is None:
        # Heuristic fallback
        rel = state["relationship_snapshot"] or {}
        tier = state["identity_tier"]
        rw = float(rel.get("relationship_weight", 0.5))
        reply = tier == 1 or rw >= 0.7
        return {
            "reply_required": reply,
            "decision_reason": "Heuristic: tier/relationship weight decision.",
        }
    try:
        payload = {
            "subject": state["subject"],
            "body_preview": state["body_preview"],
            "category": state["category"],
            "summary": state["summary"],
            "sender_name": state["sender_name"],
            "sender_email": state["sender_email"],
            "identity_tier": state["identity_tier"],
            "relationship_weight": (state["relationship_snapshot"] or {}).get("relationship_weight"),
            "shared_events_count": len(state["shared_events"]),
            "conversation_history": state["conversation_context"] or "(no prior thread history)",
        }
        messages = [
            SystemMessage(content=DECISION_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)[:MAX_RESPONSE_CONTEXT_CHARS]),
        ]
        result: DecisionOutput = _decision_chain.invoke(messages)
        tier = state.get("identity_tier", 3)
        reply_required = result.reply_required or (tier == 1)
        decision_reason = result.decision_reason if reply_required == result.reply_required else "发件人为高权重联系人（教授/导师/主管），建议正式回复。"
        return {"reply_required": reply_required, "decision_reason": decision_reason}
    except Exception as exc:
        logger.warning("Decision node failed, defaulting to no reply: %s", exc)
        tier = state.get("identity_tier", 3)
        return {
            "reply_required": tier == 1,
            "decision_reason": "Decision LLM unavailable." if tier != 1 else "发件人为高权重联系人（教授/导师/主管），建议正式回复。",
        }


def _draft_node(state: ReviewState) -> dict:
    """LangGraph node: generate tone templates (only runs when reply is needed)."""
    if _draft_llm is None:
        draft = _heuristic_response(
            category=state["category"],
            summary=state["summary"],
            attachment_status=state["attachment_status"],
            relationship_snapshot=state["relationship_snapshot"],
            top_schedule_candidate=state["top_schedule_candidate"],
            writing_profile=state["writing_profile"],
            identity_tier=state["identity_tier"],
            shared_events=state["shared_events"],
        )
        return {"draft": draft}

    try:
        payload: dict[str, Any] = {
            "subject": state["subject"],
            "body_preview": state["body_preview"],
            "classifier": {"category": state["category"], "summary": state["summary"]},
            "attachment_status": state["attachment_status"],
            "relationship_snapshot": state["relationship_snapshot"] or {},
            "top_schedule_candidate": state["top_schedule_candidate"] or {},
            "writing_profile": state["writing_profile"] or {},
            "identity_tier": state["identity_tier"],
            "shared_org_members": state["shared_org_members"],
            "shared_events_count": len(state["shared_events"]),
            "conversation_history": state["conversation_context"] or "(no prior thread history)",
        }
        messages = [
            SystemMessage(content=DRAFT_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)[:MAX_RESPONSE_CONTEXT_CHARS]),
        ]
        raw = _draft_llm.invoke(messages)
        content = raw.content if hasattr(raw, "content") else str(raw)
        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.rsplit("```", 1)[0].strip()
        parsed = json.loads(content)
        tone_templates: dict[str, str] = parsed.get("tone_templates", {})
        for k in ("professional", "casual", "colloquial"):
            tone_templates.setdefault(k, "")
        return {"draft": {"tone_templates": tone_templates}}
    except Exception:
        draft = _heuristic_response(
            category=state["category"],
            summary=state["summary"],
            attachment_status=state["attachment_status"],
            relationship_snapshot=state["relationship_snapshot"],
            top_schedule_candidate=state["top_schedule_candidate"],
            writing_profile=state["writing_profile"],
            identity_tier=state["identity_tier"],
            shared_events=state["shared_events"],
        )
        return {"draft": draft}


def _route_after_decision(state: ReviewState) -> str:
    return "draft" if state.get("reply_required") else "end"


def _build_review_graph() -> Any:
    g: StateGraph = StateGraph(ReviewState)
    g.add_node("decision", _decision_node)
    g.add_node("draft", _draft_node)
    g.add_edge(START, "decision")
    g.add_conditional_edges("decision", _route_after_decision, {"draft": "draft", "end": END})
    g.add_edge("draft", END)
    return g.compile()


_review_graph = _build_review_graph()


def _run_review_loop(
    *,
    subject: str,
    body_preview: str,
    category: str,
    summary: str,
    attachment_status: str,
    relationship_snapshot: Optional[dict[str, Any]],
    top_schedule_candidate: Optional[dict[str, Any]],
    writing_profile: dict[str, Any],
    identity_tier: int,
    shared_org_members: list[str],
    shared_events: list[str],
    conversation_context: str,
    sender_name: str,
    sender_email: str,
) -> dict[str, Any]:
    """Run decision → (if needed) draft and return final state."""
    initial: ReviewState = {
        "subject": subject,
        "body_preview": body_preview,
        "category": category,
        "summary": summary,
        "attachment_status": attachment_status,
        "relationship_snapshot": relationship_snapshot or {},
        "top_schedule_candidate": top_schedule_candidate,
        "writing_profile": writing_profile,
        "identity_tier": identity_tier,
        "shared_org_members": shared_org_members,
        "shared_events": shared_events,
        "conversation_context": conversation_context,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "reply_required": False,
        "decision_reason": "",
        "draft": None,
    }
    final: ReviewState = _review_graph.invoke(initial)
    return {
        "reply_required": final["reply_required"],
        "decision_reason": final["decision_reason"],
        "tone_templates": (final["draft"] or {}).get("tone_templates", {}),
    }


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

    # Short-circuit for bulk/broadcast senders — no reply needed.
    from services.orchestration import BULK_SENDER_RE
    if BULK_SENDER_RE.search(sender_email or ""):
        output = _heuristic_response(
            category=classifier.category,
            summary=classifier.summary,
            attachment_status=attachment_status,
            relationship_snapshot=None,
            top_schedule_candidate=None,
            writing_profile={},
            identity_tier=3,
        )
        output["reply_required"] = False
        output["decision_reason"] = "Bulk or broadcast sender — no reply required."
        set_non_current_reply(session, email_id)
        session.add(ReplySuggestion(
            run_id=run_id,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            reply_required=False,
            decision_reason=output["decision_reason"],
            tone_templates=output["tone_templates"],
            is_current=True,
        ))
        return {**output, "identity_tier": 3, "preferred_tone_key": "professional",
                "shared_org_members": [], "shared_events": [], "relationship_snapshot": None,
                "top_schedule_candidate": None, "writing_profile": {},
                "produced_at_utc": datetime.now(timezone.utc).isoformat()}
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

    # Tier 0 = system/broadcast sender — short-circuit, no reply needed.
    if identity_tier == 0:
        reason = "Automated or broadcast sender — no reply required."
        set_non_current_reply(session, email_id)
        session.add(ReplySuggestion(
            run_id=run_id, trace_id=trace_id, email_id=email_id, user_id=user_id,
            reply_required=False, decision_reason=reason, tone_templates={}, is_current=True,
        ))
        return {
            "reply_required": False, "decision_reason": reason, "tone_templates": {},
            "identity_tier": 0, "preferred_tone_key": "professional",
            "shared_org_members": shared_org_members, "shared_events": shared_events,
            "relationship_snapshot": relationship_snapshot, "top_schedule_candidate": None,
            "writing_profile": {}, "produced_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    top_candidate = get_current_top_schedule_candidate(session, email_id)
    writing_profile = _profile_to_dict(get_user_writing_profile(session, user_id))
    top_schedule_candidate = None
    if top_candidate:
        top_schedule_candidate = {
            "candidate_id": top_candidate.candidate_id,
            "title": top_candidate.title,
            "action": top_candidate.action,
        }

    # RAG: retrieve conversation thread history scoped to conversation_id.
    # This ensures context comes only from the current reply chain, not any
    # unrelated email from the same sender.
    conversation_context = _retrieve_conversation_context(session, email)

    # Draft → Critic review loop (max 2 iterations).
    # Falls back to heuristic internally if LLM is unavailable.
    output = _run_review_loop(
        subject=email.subject or "",
        body_preview=email.body_preview or "",
        sender_name=email.sender_name or "",
        sender_email=sender_email or "",
        category=classifier.category,
        summary=classifier.summary,
        attachment_status=attachment_status,
        relationship_snapshot=relationship_snapshot,
        top_schedule_candidate=top_schedule_candidate,
        writing_profile=writing_profile,
        identity_tier=identity_tier,
        shared_org_members=shared_org_members,
        shared_events=shared_events,
        conversation_context=conversation_context,
    )
    if not output:
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
