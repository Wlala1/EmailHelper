from typing import TypedDict, Literal, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class EmailClassification(TypedDict):
    need_reply: bool
    urgency: Literal["low", "medium", "high", "critical"]
    topic: str
    summary: Optional[str] = None

class EmailMessage(TypedDict):
    id: str
    from_addr: str
    to: List[str] = []
    subject: str = ""
    body: str = ""
    received_at: Optional[str] = None
    attachments: List[str] = []
    labels: List[str] = []

    # Classification
    classification: EmailClassification | None

    # Reply
    suggested_reply: Optional[str] = None
    reply_sent: bool = False

    # Task
    task_status: Optional[str] = None   # Pending | Completed
    task_due_date: Optional[str] = None
    task_row: Optional[int] = None       # Google Sheets row index

    # Processing metadata
    processed_at: Optional[str] = None
    error: Optional[str] = None

class UserPreferences(TypedDict):
    user_id: str = "self"
    preferred_style: str = "warm"            # formal | warm | concise
    preferred_summary_length: str = "short"  # short | medium | long
    auto_send_reply: bool = False
    reply_language: str = "zh"
    follow_up_categories: List[str] = ["fundraiser", "customer"]
    last_updated: Optional[str] = None


class ProcessRequest(TypedDict):
    email_id: str
    force_reprocess: bool = False


class ReplyConfirmRequest(TypedDict):
    email_id: str
    reply_text: str


class TaskUpdateRequest(TypedDict):
    email_id: str
    status: str  # Pending | Completed
