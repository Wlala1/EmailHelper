from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from config import ATTACHMENTS_DIR


def _safe_filename(filename: str) -> str:
    keep = "".join(ch for ch in filename if ch.isalnum() or ch in (".", "_", "-"))
    return keep or "attachment.bin"


def store_attachment_content(attachment_id: str, name: str, content_base64: Optional[str]) -> Optional[str]:
    if not content_base64:
        return None

    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(name)
    output_path = ATTACHMENTS_DIR / f"{attachment_id}_{safe_name}"
    with open(output_path, "wb") as handle:
        handle.write(base64.b64decode(content_base64))
    return str(output_path)


def local_path_exists(path: Optional[str]) -> bool:
    return bool(path and Path(path).exists())
