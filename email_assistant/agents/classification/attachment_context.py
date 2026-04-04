from __future__ import annotations

from typing import Any, Optional

from agents.classification.common import (
    MIN_HEURISTIC_ATTACHMENT_SECTION_CHARS,
    AttachmentAudit,
    AttachmentContextBundle,
    ParsedAttachmentRecord,
    format_values,
    truncate_chars,
)
from agents.classification.llm import llm_summarize_attachment_sections
from config import MAX_ATTACHMENT_CONTEXT_CHARS


def attachment_source(record: ParsedAttachmentRecord) -> dict[str, Any]:
    parsed = record.parsed
    return {
        "attachment_id": parsed.attachment_id,
        "name": record.name,
        "doc_type": parsed.doc_type or "unknown",
        "topics": [str(item) for item in parsed.topics if str(item).strip()],
        "named_entities": [str(item) for item in parsed.named_entities if str(item).strip()],
        "time_expressions": [str(item) for item in parsed.time_expressions if str(item).strip()],
        "content": parsed.extracted_text or "",
        "raw_chars": len(parsed.extracted_text or ""),
    }


def build_inline_attachment_section(source: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"[Attachment {source['attachment_id']}]",
            f"Name: {source['name']}",
            f"Doc Type: {source['doc_type']}",
            f"Topics: {format_values(source['topics'])}",
            f"Time Expressions: {format_values(source['time_expressions'])}",
            f"Named Entities: {format_values(source['named_entities'])}",
            "Content:",
            source["content"] or "[empty attachment content]",
        ]
    ).strip()


def build_summary_attachment_section(source: dict[str, Any], summary: str) -> str:
    return "\n".join(
        [
            f"[Attachment {source['attachment_id']}]",
            f"Name: {source['name']}",
            f"Doc Type: {source['doc_type']}",
            "Summary:",
            summary.strip() or "[empty summary]",
        ]
    ).strip()


def build_attachment_context_from_sections(
    sources: list[dict[str, Any]],
    sections_by_id: dict[str, str],
    *,
    mode: str,
) -> AttachmentContextBundle:
    ordered_sections: list[str] = []
    audits: dict[str, AttachmentAudit] = {}
    raw_chars = sum(source["raw_chars"] for source in sources)
    for source in sources:
        attachment_id = source["attachment_id"]
        section = sections_by_id.get(attachment_id, "").strip()
        audits[attachment_id] = AttachmentAudit(
            raw_chars=source["raw_chars"],
            included_chars=len(section),
            included_mode=mode,
        )
        if section:
            ordered_sections.append(section)
    text = "\n\n".join(ordered_sections)
    return AttachmentContextBundle(text=text, mode=mode, raw_chars=raw_chars, context_chars=len(text), audits=audits)


def build_inline_attachment_context(sources: list[dict[str, Any]]) -> AttachmentContextBundle:
    return build_attachment_context_from_sections(
        sources,
        {source["attachment_id"]: build_inline_attachment_section(source) for source in sources},
        mode="inline",
    )


def build_heuristic_attachment_section(source: dict[str, Any], limit: int) -> str:
    if limit <= 0:
        return ""
    header = "\n".join(
        [
            f"[Attachment {source['attachment_id']}]",
            f"Name: {source['name']}",
            f"Doc Type: {source['doc_type']}",
            f"Topics: {format_values(source['topics'])}",
            f"Time Expressions: {format_values(source['time_expressions'])}",
            f"Named Entities: {format_values(source['named_entities'])}",
            "Summary:",
        ]
    )
    excerpt_budget = max(0, limit - len(header) - 1)
    excerpt = truncate_chars(source["content"], excerpt_budget)
    if excerpt:
        return f"{header}\n{excerpt}"
    return truncate_chars(header, limit)


def build_heuristic_attachment_context(sources: list[dict[str, Any]]) -> AttachmentContextBundle:
    sections: dict[str, str] = {}
    remaining = MAX_ATTACHMENT_CONTEXT_CHARS
    total_sources = len(sources)
    for index, source in enumerate(sources):
        separator_len = 2 if sections else 0
        if remaining <= separator_len:
            break
        remaining_sources = total_sources - index - 1
        reserved = remaining_sources * MIN_HEURISTIC_ATTACHMENT_SECTION_CHARS
        section_budget = max(0, remaining - separator_len - reserved)
        if section_budget <= 0:
            section_budget = remaining - separator_len
        section = build_heuristic_attachment_section(source, section_budget)
        if not section:
            continue
        max_available = remaining - separator_len
        if len(section) > max_available:
            section = truncate_chars(section, max_available)
        if not section:
            break
        sections[source["attachment_id"]] = section
        remaining -= separator_len + len(section)
    if not sections and sources:
        first = sources[0]
        sections[first["attachment_id"]] = build_heuristic_attachment_section(first, MAX_ATTACHMENT_CONTEXT_CHARS)
    bundle = build_attachment_context_from_sections(sources, sections, mode="heuristic_fallback")
    if bundle.context_chars > MAX_ATTACHMENT_CONTEXT_CHARS:
        truncated = truncate_chars(bundle.text, MAX_ATTACHMENT_CONTEXT_CHARS)
        return AttachmentContextBundle(
            text=truncated,
            mode=bundle.mode,
            raw_chars=bundle.raw_chars,
            context_chars=len(truncated),
            audits=bundle.audits,
        )
    return bundle


def build_attachment_context(parsed_records: list[ParsedAttachmentRecord]) -> AttachmentContextBundle:
    if not parsed_records:
        return AttachmentContextBundle(text="", mode="inline", raw_chars=0, context_chars=0, audits={})
    sources = [attachment_source(record) for record in parsed_records]
    inline_bundle = build_inline_attachment_context(sources)
    if inline_bundle.context_chars <= MAX_ATTACHMENT_CONTEXT_CHARS:
        return inline_bundle
    llm_bundle = llm_summarize_attachment_sections(
        sources=sources,
        build_bundle=build_attachment_context_from_sections,
        build_summary_section=build_summary_attachment_section,
    )
    if llm_bundle is not None:
        return llm_bundle
    return build_heuristic_attachment_context(sources)


def attachment_result_to_dict(record: ParsedAttachmentRecord, audit: Optional[AttachmentAudit]) -> dict[str, Any]:
    parsed = record.parsed
    return {
        "attachment_id": parsed.attachment_id,
        "doc_type": parsed.doc_type,
        "relevance_score": parsed.relevance_score,
        "topics": parsed.topics,
        "named_entities": parsed.named_entities,
        "time_expressions": parsed.time_expressions,
        "extracted_text": parsed.extracted_text,
        "raw_chars": audit.raw_chars if audit else len(parsed.extracted_text or ""),
        "included_chars": audit.included_chars if audit else 0,
        "included_mode": audit.included_mode if audit else None,
    }
