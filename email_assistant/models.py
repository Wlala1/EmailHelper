from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    primary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_login_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Email(Base):
    __tablename__ = "emails"

    email_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    graph_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    graph_immutable_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    internet_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_content_type: Mapped[str] = mapped_column(String(64), default="text/html")
    body_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    mailbox_folder: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    graph_parent_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mailbox_last_modified_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_mode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    attachments: Mapped[list["Attachment"]] = relationship("Attachment", back_populates="email", cascade="all, delete-orphan")
    recipients: Mapped[list["EmailRecipient"]] = relationship("EmailRecipient", back_populates="email", cascade="all, delete-orphan")


class EmailRecipient(Base):
    __tablename__ = "email_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.email_id"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_type: Mapped[str] = mapped_column(String(16), default="to")
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    email: Mapped["Email"] = relationship("Email", back_populates="recipients")


class Attachment(Base):
    __tablename__ = "attachments"

    attachment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.email_id"), index=True)
    graph_attachment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_inline: Mapped[bool] = mapped_column(Boolean, default=False)
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    email: Mapped["Email"] = relationship("Email", back_populates="attachments")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    upstream_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="started", index=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ClassifierResult(Base):
    __tablename__ = "classifier_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    urgency_score: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    sender_role: Mapped[str] = mapped_column(String(128), nullable=False)
    named_entities: Mapped[list] = mapped_column(JSON, default=list)
    time_expressions: Mapped[list] = mapped_column(JSON, default=list)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CategoryDefinition(Base):
    __tablename__ = "category_definitions"
    __table_args__ = (UniqueConstraint("user_id", "category_name", name="uq_category_definitions_user_name"),)

    category_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    category_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category_description: Mapped[str] = mapped_column(Text, nullable=False)
    created_from_email_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CategorySuggestion(Base):
    __tablename__ = "category_suggestions"

    suggestion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    category_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category_description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_email_ids: Mapped[list] = mapped_column(JSON, default=list)
    supporting_subjects: Mapped[list] = mapped_column(JSON, default=list)
    rationale_keywords: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    process_limit: Mapped[int] = mapped_column(Integer, default=0)
    created_from_email_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promoted_category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AttachmentResult(Base):
    __tablename__ = "attachment_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    attachment_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    named_entities: Mapped[list] = mapped_column(JSON, default=list)
    time_expressions: Mapped[list] = mapped_column(JSON, default=list)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RelationshipObservation(Base):
    __tablename__ = "relationship_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    person_email: Mapped[str] = mapped_column(String(255), index=True)
    person_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    person_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    organisation_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organisation_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(64), default="email_from")
    signal_weight: Mapped[float] = mapped_column(Float, default=1.0)
    observed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduleCandidate(Base):
    __tablename__ = "schedule_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    conflict_score: Mapped[float] = mapped_column(Float, default=0.0)
    recommendation_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    show_as: Mapped[str] = mapped_column(String(64), default="tentative")
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outlook_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outlook_weblink: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    write_status: Mapped[str] = mapped_column(String(32), default="pending")
    last_write_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReplySuggestion(Base):
    __tablename__ = "reply_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    reply_required: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone_templates: Mapped[dict] = mapped_column(JSON, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserMailboxAccount(Base):
    __tablename__ = "user_mailbox_accounts"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="ms_graph")
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    token_expires_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserMailboxState(Base):
    __tablename__ = "user_mailbox_state"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    mailbox_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    bootstrap_status: Mapped[str] = mapped_column(String(32), default="not_started", index=True)
    bootstrap_started_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bootstrap_completed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bootstrap_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    polling_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_poll_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inbox_delta_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_delta_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserWritingProfile(Base):
    __tablename__ = "user_writing_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    preferred_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tone_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avg_length_bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    greeting_patterns: Mapped[list] = mapped_column(JSON, default=list)
    closing_patterns: Mapped[list] = mapped_column(JSON, default=list)
    signature_blocks: Mapped[list] = mapped_column(JSON, default=list)
    cta_patterns: Mapped[list] = mapped_column(JSON, default=list)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    profile_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    last_profiled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Behavioral preference vector (Phase E): updated from user feedback events.
    # Schema: {
    #   "tone_accept_rates": {"professional": 0.8, "casual": 0.5, "colloquial": 0.3},
    #   "schedule_accept_rate": 0.6,
    #   "feedback_count": 12,
    # }
    preference_vector: Mapped[dict] = mapped_column(JSON, default=dict)
    preference_vector_updated_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserFeedbackEvent(Base):
    """Records explicit or implicit user feedback on agent suggestions."""
    __tablename__ = "user_feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    # What the user gave feedback on.
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Values: "schedule_candidate" | "reply_suggestion" | "tone_template" | "draft_write"
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Values: "accepted" | "rejected" | "edited" | "dismissed" | "deferred"
    feedback_signal: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Optional metadata, e.g. {"tone_key": "professional", "conflict_score_at_time": 0.3}
    feedback_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    sync_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    cursor_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReplyDraftWrite(Base):
    __tablename__ = "reply_draft_writes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reply_suggestion_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)
    policy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    draft_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    outlook_draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outlook_web_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemLease(Base):
    __tablename__ = "system_leases"

    lock_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    locked_until_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
