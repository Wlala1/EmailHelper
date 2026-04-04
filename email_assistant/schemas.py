from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from config import OUMA_SCHEMA_VERSION


class AgentName(str, Enum):
    intake = "intake"
    classifier = "classifier"
    attachment = "attachment"
    relationship_graph = "relationship_graph"
    schedule = "schedule"
    response = "response"


class AgentRunStatus(str, Enum):
    started = "started"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class ScheduleAction(str, Enum):
    create_tentative_event = "create_tentative_event"
    suggest_only = "suggest_only"
    ignore = "ignore"


class WriteStatus(str, Enum):
    pending = "pending"
    written = "written"
    failed = "failed"


class OUMAEnvelope(BaseModel):
    schema_version: str = Field(default=OUMA_SCHEMA_VERSION)
    trace_id: str
    run_id: str
    email_id: str
    user_id: str
    agent_name: AgentName
    produced_at_utc: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != OUMA_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {OUMA_SCHEMA_VERSION}")
        return value


class UserPayload(BaseModel):
    user_id: str
    primary_email: Optional[str] = None
    display_name: Optional[str] = None
    timezone: Optional[str] = None


class EmailPayload(BaseModel):
    email_id: str
    graph_message_id: Optional[str] = None
    graph_immutable_id: Optional[str] = None
    internet_message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: str
    subject: Optional[str] = None
    body_content_type: str = "text/html"
    body_content: Optional[str] = None
    body_preview: Optional[str] = None
    received_at_utc: datetime
    has_attachments: bool = False


class EmailRecipientPayload(BaseModel):
    recipient_email: str
    recipient_name: Optional[str] = None
    recipient_type: str = "to"


class AttachmentPayload(BaseModel):
    attachment_id: str
    graph_attachment_id: Optional[str] = None
    name: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    is_inline: bool = False
    local_path: Optional[str] = None
    content_base64: Optional[str] = None


class ClassifierOutput(BaseModel):
    category: str
    category_description: Optional[str] = None
    urgency_score: float
    summary: str
    sender_role: str
    named_entities: list[str] = Field(default_factory=list)
    time_expressions: list[str] = Field(default_factory=list)


class AttachmentResultItem(BaseModel):
    attachment_id: str
    doc_type: Optional[str] = None
    relevance_score: float = 0.0
    topics: list[str] = Field(default_factory=list)
    named_entities: list[str] = Field(default_factory=list)
    time_expressions: list[str] = Field(default_factory=list)
    extracted_text: Optional[str] = None


class RelationshipObservationItem(BaseModel):
    person_email: str
    person_name: Optional[str] = None
    person_role: Optional[str] = None
    organisation_name: Optional[str] = None
    organisation_domain: Optional[str] = None
    signal_type: str = "email_from"
    signal_weight: float = 1.0
    observed_at_utc: datetime


class ScheduleCandidateItem(BaseModel):
    candidate_id: str
    source: str
    title: str
    start_time_utc: datetime
    end_time_utc: datetime
    source_timezone: str
    is_all_day: bool = False
    location: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    confidence: float
    conflict_score: float
    recommendation_rank: Optional[int] = None
    action: ScheduleAction
    show_as: str = "tentative"
    transaction_id: str
    outlook_event_id: Optional[str] = None
    outlook_weblink: Optional[str] = None
    write_status: WriteStatus = WriteStatus.pending
    last_write_error: Optional[str] = None


class ResponseOutput(BaseModel):
    reply_required: bool
    decision_reason: Optional[str] = None
    tone_templates: dict[str, str] = Field(default_factory=dict)


class BranchStatusResponse(BaseModel):
    trace_id: str
    email_id: str
    branch_statuses: dict[str, str]
    current_classifier: Optional[dict[str, Any]] = None
    current_attachment_status: Optional[str] = None
    top_schedule_candidate: Optional[dict[str, Any]] = None
    current_response: Optional[dict[str, Any]] = None
