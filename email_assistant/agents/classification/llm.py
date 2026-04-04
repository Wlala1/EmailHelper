from __future__ import annotations

import json
from typing import Any, Optional

from openai import OpenAI

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

_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None


def llm_classify(
    *,
    combined_text: str,
    existing_categories: list[dict[str, str]],
    subject: str,
) -> Optional[dict[str, Any]]:
    if _client is None:
        return None
    try:
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "existing_categories": existing_categories,
                            "subject": subject,
                            "content": combined_text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        existing_names = {item["category_name"] for item in existing_categories}
        is_new_category = bool(data.get("is_new_category", False))

        selected_category_name = normalize_category_name(str(data.get("selected_category_name", "")).strip())
        new_category_name = normalize_category_name(str(data.get("new_category_name", "")).strip())
        new_category_description = str(data.get("new_category_description", "")).strip()

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
            "urgency_score": float(data.get("urgency_score", 0.5)),
            "summary": str(data.get("summary", "")).strip() or "未生成摘要",
            "sender_role": str(data.get("sender_role", "Unknown")).strip() or "Unknown",
            "named_entities": [str(item) for item in data.get("named_entities", [])][:30],
            "time_expressions": [str(item) for item in data.get("time_expressions", [])][:20],
        }
    except Exception:
        return None


def llm_summarize_attachment_sections(
    *,
    sources: list[dict[str, Any]],
    build_bundle,
    build_summary_section,
) -> Optional[AttachmentContextBundle]:
    if _client is None:
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
        response = _client.chat.completions.create(
            model=OPENAI_ATTACHMENT_SUMMARY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": ATTACHMENT_SUMMARY_PROMPT.format(max_chars=MAX_ATTACHMENT_CONTEXT_CHARS),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        summaries = {
            str(item.get("attachment_id", "")).strip(): str(item.get("summary", "")).strip()
            for item in data.get("attachments", [])
            if str(item.get("attachment_id", "")).strip() and str(item.get("summary", "")).strip()
        }
        if not summaries:
            return None
        bundle = build_bundle(
            sources,
            {
                source["attachment_id"]: build_summary_section(source, summaries.get(source["attachment_id"], ""))
                for source in sources
            },
            mode="summarized",
        )
        if bundle.context_chars > MAX_ATTACHMENT_CONTEXT_CHARS or len(bundle.audits) != len(sources):
            return None
        return bundle
    except Exception:
        return None
