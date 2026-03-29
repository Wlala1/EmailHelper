from pathlib import Path

from agents.input_handler import parse_attachment


def test_parse_text_attachment_extracts_time_and_entities(tmp_path: Path):
    file_path = tmp_path / "notice.txt"
    file_path.write_text(
        "Call for Papers from Prof Lim.\nDeadline: 2026-04-15\nContact: prof.lim@nus.edu.sg",
        encoding="utf-8",
    )

    parsed = parse_attachment(
        attachment_id="att-1",
        name="notice.txt",
        path=str(file_path),
        content_type="text/plain",
        sender_email="prof.lim@nus.edu.sg",
    )

    assert parsed.attachment_id == "att-1"
    assert "2026-04-15" in parsed.time_expressions
    assert "prof.lim@nus.edu.sg" in parsed.named_entities
    assert parsed.extracted_text
