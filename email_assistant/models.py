from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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

    category_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    category_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category_description: Mapped[str] = mapped_column(Text, nullable=False)
    created_from_email_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
