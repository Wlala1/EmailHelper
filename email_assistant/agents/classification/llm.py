from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agents.classification.common import (
    ATTACHMENT_SUMMARY_PROMPT,
    CLASSIFIER_PROMPT,
    CLASSIFIER_TOOL_PROMPT,
    MAX_ATTACHMENT_SUMMARY_SOURCE_CHARS,
    AttachmentContextBundle,
    normalize_category_name,
    truncate_chars,
)
from config import (
    MAX_ATTACHMENT_CONTEXT_CHARS,
    OPENAI_API_KEY,
    OPENAI_ATTACHMENT_SUMMARY_MODEL,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

logger = logging.getLogger(__name__)


# ── Pydantic schemas for structured output ──────────────────────────────────

class ClassificationOutput(BaseModel):
    selected_category_name: str = Field(default="")
    new_category_name: str = Field(default="")
    new_category_description: str = Field(default="")
    is_new_category: bool = Field(default=False)
    urgency_score: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = Field(default="")
    named_entities: list[str] = Field(default_factory=list)
    time_expressions: list[str] = Field(default_factory=list)


class AttachmentSummaryItem(BaseModel):
    attachment_id: str = Field(default="")
    summary: str = Field(default="")


class AttachmentSummaryOutput(BaseModel):
    attachments: list[AttachmentSummaryItem] = Field(default_factory=list)


# ── LangChain clients ────────────────────────────────────────────────────────

def _make_llm(model: str, temperature: float = 0.1) -> Optional[ChatOpenAI]:
    if not OPENAI_API_KEY:
        return None
    kwargs: dict[str, Any] = dict(
        api_key=OPENAI_API_KEY,
        model=model,
        temperature=temperature,
    )
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    return ChatOpenAI(**kwargs)


_classify_llm = _make_llm(OPENAI_MODEL, temperature=0.1)
_summary_llm = _make_llm(OPENAI_ATTACHMENT_SUMMARY_MODEL, temperature=0.1)

# Chains with structured output — validated by Pydantic, auto-retry on bad JSON
_classifier_chain = _classify_llm.with_structured_output(ClassificationOutput) if _classify_llm else None
_summary_chain = _summary_llm.with_structured_output(AttachmentSummaryOutput) if _summary_llm else None


# ── Public functions ─────────────────────────────────────────────────────────

def llm_classify(
    *,
    combined_text: str,
    existing_categories: list[dict[str, str]],
    subject: str,
) -> Optional[dict[str, Any]]:
    if _classifier_chain is None:
        return None
    try:
        messages = [
            SystemMessage(content=CLASSIFIER_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "existing_categories": existing_categories,
                        "subject": subject,
                        "content": combined_text,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        data: ClassificationOutput = _classifier_chain.invoke(messages)

        existing_names = {item["category_name"] for item in existing_categories}
        is_new_category = data.is_new_category

        selected_category_name = normalize_category_name(data.selected_category_name.strip())
        new_category_name = normalize_category_name(data.new_category_name.strip())
        new_category_description = data.new_category_description.strip()

        if is_new_category:
            category_name = new_category_name or selected_category_name
            category_description = new_category_description or f"Emails related to {category_name}."
        else:
            if selected_category_name not in existing_names and existing_names:
                category_name = next(iter(existing_names))
            elif not existing_names:
                is_new_category = True
                category_name = new_category_name or selected_category_name or normalize_category_name(subject)
            else:
                category_name = selected_category_name

            if is_new_category:
                category_description = new_category_description or f"Emails related to {category_name}."
            else:
                category_description = next(
                    (
                        item["category_description"]
                        for item in existing_categories
                        if item["category_name"] == category_name
                    ),
                    f"Emails related to {category_name}.",
                )

        return {
            "category_name": normalize_category_name(category_name),
            "category_description": category_description.strip(),
            "is_new_category": is_new_category,
            "urgency_score": float(data.urgency_score),
            "summary": data.summary.strip() or "未生成摘要",
            "named_entities": [str(e) for e in data.named_entities][:10],
            "time_expressions": [str(t) for t in data.time_expressions][:10],
        }
    except Exception:
        return None


def llm_summarize_attachment_sections(
    *,
    sources: list[dict[str, Any]],
    build_bundle,
    build_summary_section,
) -> Optional[AttachmentContextBundle]:
    if _summary_chain is None:
        return None

    payload = {
        "max_chars": MAX_ATTACHMENT_CONTEXT_CHARS,
        "attachments": [
            {
                "attachment_id": source["attachment_id"],
                "name": source["name"],
                "doc_type": source["doc_type"],
                "topics": source["topics"],
                "time_expressions": source["time_expressions"],
                "named_entities": source["named_entities"],
                "content_excerpt": truncate_chars(source["content"], MAX_ATTACHMENT_SUMMARY_SOURCE_CHARS),
            }
            for source in sources
        ],
    }
    try:
        messages = [
            SystemMessage(
                content=ATTACHMENT_SUMMARY_PROMPT.format(max_chars=MAX_ATTACHMENT_CONTEXT_CHARS)
            ),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        data: AttachmentSummaryOutput = _summary_chain.invoke(messages)

        summaries = {
            item.attachment_id.strip(): item.summary.strip()
            for item in data.attachments
            if item.attachment_id.strip() and item.summary.strip()
        }
        if not summaries:
            return None

        bundle = build_bundle(
            sources,
            {
                source["attachment_id"]: build_summary_section(
                    source, summaries.get(source["attachment_id"], "")
                )
                for source in sources
            },
            mode="summarized",
        )
        if bundle.context_chars > MAX_ATTACHMENT_CONTEXT_CHARS or len(bundle.audits) != len(sources):
            return None
        return bundle
    except Exception:
        return None


# ── Tool-calling classifier ──────────────────────────────────────────────────

def _build_classifier_tools(session: Session, user_id: str, result_sink: list[dict]) -> list:
    """Build classification tools with session/user_id bound via closure."""
    from repositories import get_category_by_name, get_category_definitions

    @tool
    def search_categories(query: str) -> str:
        """Search existing email categories by keyword.
        Use 2-3 keywords from the email subject or body.
        Returns up to 5 matching categories with their descriptions.
        """
        all_cats = get_category_definitions(session, user_id)
        words = query.lower().split()
        matched = [
            c for c in all_cats
            if any(
                w in c.category_name.lower() or w in (c.category_description or "").lower()
                for w in words
            )
        ]
        # Fall back to full list (capped) if no keyword match
        results = matched[:5] if matched else all_cats[:5]
        return json.dumps([
            {"category_name": c.category_name, "category_description": c.category_description}
            for c in results
        ])

    @tool
    def get_category_details(category_name: str) -> str:
        """Get the full description of a specific category by exact name.
        Use this to confirm whether a category truly fits the email before assigning it.
        Returns the category details or an error if not found.
        """
        cat = get_category_by_name(session, user_id, category_name)
        if cat is None:
            return json.dumps({"error": f"Category '{category_name}' not found."})
        return json.dumps({
            "category_name": cat.category_name,
            "category_description": cat.category_description,
        })

    @tool
    def finalize_classification(result_json: str) -> str:
        """Submit the final classification result. Call this ONCE as your last action.
        result_json must be a JSON object with these fields:
          - category_name (str): title-case, ≤ 64 chars
          - is_new_category (bool): true only if no existing category fits
          - category_description (str): description for new categories; empty string for existing ones
          - urgency_score (float 0.0-1.0): 0.9+ for deadlines, 0.5 informational, 0.2 newsletters
          - summary (str): concise 2-3 sentence Chinese summary
          - sender_role (str): Format "Org · Role". (1) Org: always extract from the email domain suffix — strip subdomains to registrar domain ("comp.nus.edu.sg" → "nus.edu.sg"), use SLD as label ("NUS", "Google", "Microsoft"); short SLD ≤4 chars → UPPERCASE, longer → Title Case. (2) Role: look at sender display name and email body/signature first ("Prof Tan Wei", "Recruiter", "PhD Student", "Lee Jin Xing"); if none found, fall back to email local-part before @ ("john.smith" → "John Smith", "hr" → "HR", "noreply" → "System"). NEVER output "Unknown", "Recipient", or empty strings.
          - named_entities (list[str]): people, orgs, emails found in the email (max 10)
          - time_expressions (list[str]): dates/times in original form (max 10)
        """
        try:
            data = json.loads(result_json)
            result_sink.clear()
            result_sink.append(data)
            return json.dumps({"accepted": True})
        except Exception as exc:
            return json.dumps({"accepted": False, "error": str(exc)})

    return [search_categories, get_category_details, finalize_classification]


def llm_classify_with_tools(
    session: Session,
    *,
    user_id: str,
    combined_text: str,
    subject: str,
) -> Optional[dict[str, Any]]:
    """Classify an email using tool-calling: the LLM actively searches existing
    categories before deciding, rather than receiving a static list in the prompt.
    Returns None on failure so the caller can fall back to llm_classify().
    """
    if _classify_llm is None:
        return None

    result_sink: list[dict] = []
    tools = _build_classifier_tools(session, user_id, result_sink)
    agent = create_react_agent(_classify_llm, tools, prompt=CLASSIFIER_TOOL_PROMPT)

    user_message = (
        f"Subject: {subject}\n\n"
        f"Email content:\n{combined_text[:4000]}"
    )
    try:
        agent.invoke({"messages": [("human", user_message)]})
    except Exception as exc:
        logger.warning("llm_classify_with_tools agent failed: %s", exc)
        return None

    if not result_sink:
        logger.warning("llm_classify_with_tools: agent did not call finalize_classification")
        return None

    raw = result_sink[0]
    try:
        category_name = normalize_category_name(str(raw.get("category_name", "") or subject))
        return {
            "category_name": category_name,
            "category_description": str(raw.get("category_description", "") or f"Emails related to {category_name}."),
            "is_new_category": bool(raw.get("is_new_category", False)),
            "urgency_score": float(raw.get("urgency_score", 0.5)),
            "summary": str(raw.get("summary", "")).strip() or "未生成摘要",
            "sender_role": str(raw.get("sender_role", "Unknown")).strip() or "Unknown",
            "named_entities": [str(e) for e in raw.get("named_entities", [])][:10],
            "time_expressions": [str(t) for t in raw.get("time_expressions", [])][:10],
        }
    except Exception as exc:
        logger.warning("llm_classify_with_tools: failed to parse result: %s", exc)
        return None
