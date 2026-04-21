from __future__ import annotations

import json
from typing import Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.classification.common import (
    ATTACHMENT_SUMMARY_PROMPT,
    CLASSIFIER_PROMPT,
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


# ── Pydantic schemas for structured output ──────────────────────────────────

class ClassificationOutput(BaseModel):
    selected_category_name: str = Field(default="")
    new_category_name: str = Field(default="")
    new_category_description: str = Field(default="")
    is_new_category: bool = Field(default=False)
    urgency_score: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = Field(default="")
    sender_role: str = Field(default="Unknown")
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
            "sender_role": data.sender_role.strip() or "Unknown",
            "named_entities": [str(e) for e in data.named_entities][:30],
            "time_expressions": [str(t) for t in data.time_expressions][:20],
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
