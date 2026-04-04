from __future__ import annotations

import argparse
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path

HEADER_NAMES_TO_ANONYMIZE = {
    "from",
    "to",
    "cc",
    "bcc",
    "reply-to",
    "sender",
    "subject",
}

ENTITY_PATTERNS = {
    "EMAIL_ADDRESS": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "PHONE_NUMBER": re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}"),
    "ORGANIZATION": re.compile(
        r"\b(?:[A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){0,4}\s(?:University|College|Institute|School|Lab|Center|Centre|Department|Inc|Ltd|Llc))\b"
    ),
    "PERSON": re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b"),
}


def _load_presidio():
    try:
        from presidio_analyzer import RecognizerResult
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Presidio dependencies are not installed. Run `pip install -r requirements.txt` first."
        ) from exc
    return RecognizerResult, AnonymizerEngine, OperatorConfig


def anonymize_text(text: str) -> str:
    RecognizerResult, AnonymizerEngine, OperatorConfig = _load_presidio()
    analyzer_results = []
    for entity_type, pattern in ENTITY_PATTERNS.items():
        for match in pattern.finditer(text):
            analyzer_results.append(
                RecognizerResult(
                    entity_type=entity_type,
                    start=match.start(),
                    end=match.end(),
                    score=0.85,
                )
            )
    analyzer_results.sort(key=lambda item: (item.start, -(item.end - item.start)))
    deduped = []
    last_end = -1
    for item in analyzer_results:
        if item.start < last_end:
            continue
        deduped.append(item)
        last_end = item.end
    operators = {
        entity_type: OperatorConfig("replace", {"new_value": f"<{entity_type}>"})
        for entity_type in ENTITY_PATTERNS
    }
    engine = AnonymizerEngine()
    result = engine.anonymize(text=text, analyzer_results=deduped, operators=operators)
    return result.text


def anonymize_message_file(input_path: Path, output_path: Path) -> None:
    message = BytesParser(policy=policy.default).parsebytes(input_path.read_bytes())
    for header_name in list(message.keys()):
        if header_name.lower() not in HEADER_NAMES_TO_ANONYMIZE:
            continue
        values = message.get_all(header_name, failobj=[])
        if not values:
            continue
        del message[header_name]
        for value in values:
            message[header_name] = anonymize_text(str(value))

    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_maintype() != "text":
            continue
        original = part.get_content()
        anonymized = anonymize_text(str(original))
        subtype = part.get_content_subtype()
        charset = part.get_content_charset() or "utf-8"
        part.set_content(anonymized, subtype=subtype, charset=charset)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(message.as_bytes(policy=policy.default))


def main() -> None:
    parser = argparse.ArgumentParser(description="Anonymize exported .eml files for demos using Presidio.")
    parser.add_argument("--input", required=True, help="Directory containing exported .eml files.")
    parser.add_argument("--output", required=True, help="Directory to write anonymized .eml files into.")
    args = parser.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    files = sorted(path for path in input_dir.rglob("*.eml") if path.is_file())
    if not files:
        raise SystemExit(f"No .eml files found under: {input_dir}")

    processed = 0
    for path in files:
        target = output_dir / path.relative_to(input_dir)
        anonymize_message_file(path, target)
        processed += 1
    print(f"Anonymized {processed} .eml file(s) into {output_dir}")


if __name__ == "__main__":
    main()
