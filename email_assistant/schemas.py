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


class ReplyReviewAction(str, Enum):
    approve = "approve"
    reject = "reject"
    defer = "defer"


class CategorySuggestionStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class CategorySuggestionAction(str, Enum):
    accept = "accept"
    reject = "reject"


class BootstrapStatus(str, Enum):
    not_started = "not_started"
    running = "running"
    completed = "completed"
    failed = "failed"


class EmailDirection(str, Enum):
    inbound = "inbound"
    outbound = "outbound"


class MailboxFolder(str, Enum):
    inbox = "inbox"
    sent = "sent"
    other = "other"


class ProcessedMode(str, Enum):
    bootstrap = "bootstrap"
    live = "live"


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
    graph_parent_folder_id: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: str
    subject: Optional[str] = None
    body_content_type: str = "text/html"
    body_content: Optional[str] = None
    body_preview: Optional[str] = None
    received_at_utc: datetime
    has_attachments: bool = False
    direction: Optional[EmailDirection] = None
    mailbox_folder: Optional[MailboxFolder] = None
    mailbox_last_modified_at_utc: Optional[datetime] = None
    processed_mode: Optional[ProcessedMode] = None


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
    attachment_context_mode: Optional[str] = None
    attachment_raw_chars: int = 0
    attachment_context_chars: int = 0


class AttachmentResultItem(BaseModel):
    attachment_id: str
    doc_type: Optional[str] = None
    relevance_score: float = 0.0
    topics: list[str] = Field(default_factory=list)
    named_entities: list[str] = Field(default_factory=list)
    time_expressions: list[str] = Field(default_factory=list)
    extracted_text: Optional[str] = None
    raw_chars: int = 0
    included_chars: int = 0
    included_mode: Optional[str] = None


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
    draft_write: Optional[dict[str, Any]] = None


class BranchStatusResponse(BaseModel):
    trace_id: str
    email_id: str
    branch_statuses: dict[str, str]
    current_classifier: Optional[dict[str, Any]] = None
    current_attachment_status: Optional[str] = None
    top_schedule_candidate: Optional[dict[str, Any]] = None
    current_response: Optional[dict[str, Any]] = None
    current_draft_write: Optional[dict[str, Any]] = None
    current_reply_review: Optional[dict[str, Any]] = None


class MailboxConnectionResponse(BaseModel):
    user_id: str
    primary_email: Optional[str] = None
    display_name: Optional[str] = None
    mailbox_connected: bool
    bootstrap_status: BootstrapStatus
    polling_enabled: bool
    inbox_delta_token: Optional[str] = None
    sent_delta_token: Optional[str] = None


class UserModeStatusResponse(BaseModel):
    user_id: str
    primary_email: Optional[str] = None
    display_name: Optional[str] = None
    mailbox_connected: bool
    bootstrap_status: BootstrapStatus
    bootstrap_started_at_utc: Optional[datetime] = None
    bootstrap_completed_at_utc: Optional[datetime] = None
    bootstrap_error: Optional[str] = None
    polling_enabled: bool
    last_poll_at_utc: Optional[datetime] = None
    active_mode: str
    preferred_language: Optional[str] = None
    tone_profile: Optional[str] = None
    avg_length_bucket: Optional[str] = None
    sample_count: int = 0


class DraftWriteStatusResponse(BaseModel):
    draft_status: str
    policy_name: str
    outlook_draft_id: Optional[str] = None
    outlook_web_link: Optional[str] = None
    error_message: Optional[str] = None


class DynamicTopicDefinition(BaseModel):
    category_name: str
    category_description: str


class CategorySuggestionRefreshRequest(BaseModel):
    sample_size: int = Field(default=50, ge=1, le=200)
    process_limit: int = Field(default=50, ge=1, le=500)


class CategorySuggestionItem(BaseModel):
    suggestion_id: str
    user_id: str
    category_name: str
    category_description: str
    supporting_email_ids: list[str] = Field(default_factory=list)
    supporting_subjects: list[str] = Field(default_factory=list)
    rationale_keywords: list[str] = Field(default_factory=list)
    status: CategorySuggestionStatus
    sample_size: int = 0
    process_limit: int = 0
    created_from_email_id: Optional[str] = None
    promoted_category_id: Optional[str] = None
    decided_at_utc: Optional[datetime] = None
    created_at_utc: datetime
    updated_at_utc: datetime


class CategorySuggestionListResponse(BaseModel):
    user_id: str
    suggestions: list[CategorySuggestionItem] = Field(default_factory=list)


class CategorySuggestionDecisionRequest(BaseModel):
    action: CategorySuggestionAction


class CategorySuggestionDecisionResponse(BaseModel):
    user_id: str
    suggestion: CategorySuggestionItem
    backfill: dict[str, Any] = Field(default_factory=dict)


class CategorySuggestionRefreshResponse(BaseModel):
    status: str = "success"
    reason: Optional[str] = None
    user_id: str
    sample_size: int
    process_limit: int
    generated_count: int = 0
    suggestions: list[CategorySuggestionItem] = Field(default_factory=list)


class DashboardSummaryCard(BaseModel):
    key: str
    label: str
    value: int
    subtitle: Optional[str] = None


class PendingReviewQueueItem(BaseModel):
    email_id: str
    user_id: str
    reply_suggestion_id: Optional[int] = None
    subject: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    received_at_utc: datetime
    decision_reason: Optional[str] = None
    draft_status: str


class DashboardSeriesItem(BaseModel):
    label: str
    value: int


class RelationshipInsightItem(BaseModel):
    person_email: str
    person_name: Optional[str] = None
    person_role: Optional[str] = None
    organisation_name: Optional[str] = None
    observation_count: int
    relationship_weight: float


class ScheduleOverviewResponse(BaseModel):
    recent_written_count: int = 0
    current_suggest_only_count: int = 0
    proactive_candidate_count: int = 0


class FeedbackOverviewResponse(BaseModel):
    total_events: int = 0
    recent_events: int = 0
    signal_counts: dict[str, int] = Field(default_factory=dict)
    preference_vector: dict[str, Any] = Field(default_factory=dict)


class UserDashboardResponse(BaseModel):
    user_id: str
    summary_cards: list[DashboardSummaryCard] = Field(default_factory=list)
    pending_review_items: list[PendingReviewQueueItem] = Field(default_factory=list)
    pending_tag_suggestions: list[CategorySuggestionItem] = Field(default_factory=list)
    category_distribution: list[DashboardSeriesItem] = Field(default_factory=list)
    top_relationships: list[RelationshipInsightItem] = Field(default_factory=list)
    schedule_overview: ScheduleOverviewResponse = Field(default_factory=ScheduleOverviewResponse)
    feedback_overview: FeedbackOverviewResponse = Field(default_factory=FeedbackOverviewResponse)
    last_refreshed_at_utc: datetime


class ReplyReviewRequest(BaseModel):
    reply_suggestion_id: int
    action: ReplyReviewAction
    tone_key: Optional[str] = None
    edited_body: Optional[str] = None


class ReplyReviewStatusResponse(BaseModel):
    email_id: str
    user_id: str
    reply_suggestion_id: int
    reply_required: bool
    decision_reason: Optional[str] = None
    tone_templates: dict[str, str] = Field(default_factory=dict)
    review_required: bool
    pending_review: bool
    latest_draft_write: Optional[dict[str, Any]] = None


class ReplyReviewResultResponse(BaseModel):
    email_id: str
    user_id: str
    reply_suggestion_id: int
    action: ReplyReviewAction
    feedback_signal: str
    draft_status: str
    policy_name: str
    outlook_draft_id: Optional[str] = None
    outlook_web_link: Optional[str] = None
    error_message: Optional[str] = None
    pending_review: bool = False
    preference_vector: dict[str, Any] = Field(default_factory=dict)


class FeedbackEventRequest(BaseModel):
    """Request body for submitting a user feedback signal on an agent suggestion."""

    user_id: str
    email_id: str
    target_type: str = Field(
        description="What is being rated: schedule_candidate | reply_suggestion | tone_template | draft_write"
    )
    target_id: str = Field(description="ID of the schedule candidate, reply suggestion, or draft being rated")
    feedback_signal: str = Field(
        description="User action: accepted | rejected | edited | dismissed | deferred"
    )
    feedback_metadata: dict = Field(
        default_factory=dict,
        description="Optional context, e.g. {tone_key: 'professional', conflict_score_at_time: 0.3}",
    )

    @field_validator("target_type")
    @classmethod
    def validate_target_type(cls, v: str) -> str:
        allowed = {"schedule_candidate", "reply_suggestion", "tone_template", "draft_write"}
        if v not in allowed:
            raise ValueError(f"target_type must be one of {allowed}")
        return v

    @field_validator("feedback_signal")
    @classmethod
    def validate_feedback_signal(cls, v: str) -> str:
        allowed = {"accepted", "rejected", "edited", "dismissed", "deferred"}
        if v not in allowed:
            raise ValueError(f"feedback_signal must be one of {allowed}")
        return v
