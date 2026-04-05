from __future__ import annotations

import re

_ENTITY_PATTERNS = {
    "EMAIL_ADDRESS": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "PHONE_NUMBER": re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}"),
    "ORGANIZATION": re.compile(
        r"\b(?:[A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){0,4}"
        r"\s(?:University|College|Institute|School|Lab|Center|Centre|Department|Inc|Ltd|Llc))\b"
    ),
    "PERSON": re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b"),
}


def anonymize_text(text: str) -> str:
    """Anonymize PII in text using Presidio if available, otherwise regex fallback."""
    if not text:
        return text
    try:
        return _anonymize_with_presidio(text)
    except Exception:
        return _anonymize_with_regex(text)


def _anonymize_with_presidio(text: str) -> str:
    from presidio_analyzer import RecognizerResult
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    analyzer_results = []
    for entity_type, pattern in _ENTITY_PATTERNS.items():
        for match in pattern.finditer(text):
            analyzer_results.append(
                RecognizerResult(entity_type=entity_type, start=match.start(), end=match.end(), score=0.85)
            )

    analyzer_results.sort(key=lambda r: (r.start, -(r.end - r.start)))
    deduped: list = []
    last_end = -1
    for item in analyzer_results:
        if item.start < last_end:
            continue
        deduped.append(item)
        last_end = item.end

    operators = {
        entity_type: OperatorConfig("replace", {"new_value": f"<{entity_type}>"})
        for entity_type in _ENTITY_PATTERNS
    }
    return AnonymizerEngine().anonymize(text=text, analyzer_results=deduped, operators=operators).text


def _anonymize_with_regex(text: str) -> str:
    for entity_type, pattern in _ENTITY_PATTERNS.items():
        text = pattern.sub(f"<{entity_type}>", text)
    return text
