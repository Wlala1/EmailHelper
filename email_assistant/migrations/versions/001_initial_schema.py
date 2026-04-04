"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("primary_email", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("last_login_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "emails",
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("graph_message_id", sa.String(255), nullable=True),
        sa.Column("graph_immutable_id", sa.String(255), nullable=True),
        sa.Column("internet_message_id", sa.String(255), nullable=True),
        sa.Column("conversation_id", sa.String(255), nullable=True),
        sa.Column("sender_name", sa.String(255), nullable=True),
        sa.Column("sender_email", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body_content_type", sa.String(64), nullable=False),
        sa.Column("body_content", sa.Text(), nullable=True),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("has_attachments", sa.Boolean(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("mailbox_folder", sa.String(32), nullable=True),
        sa.Column("graph_parent_folder_id", sa.String(255), nullable=True),
        sa.Column("mailbox_last_modified_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_mode", sa.String(32), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("email_id"),
    )
    op.create_index("ix_emails_direction", "emails", ["direction"])
    op.create_index("ix_emails_mailbox_folder", "emails", ["mailbox_folder"])
    op.create_index("ix_emails_processed_mode", "emails", ["processed_mode"])
    op.create_index("ix_emails_received_at_utc", "emails", ["received_at_utc"])
    op.create_index("ix_emails_sender_email", "emails", ["sender_email"])
    op.create_index("ix_emails_user_id", "emails", ["user_id"])

    op.create_table(
        "email_recipients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("recipient_name", sa.String(255), nullable=True),
        sa.Column("recipient_type", sa.String(16), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email_id"], ["emails.email_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_recipients_email_id", "email_recipients", ["email_id"])

    op.create_table(
        "attachments",
        sa.Column("attachment_id", sa.String(64), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("graph_attachment_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("is_inline", sa.Boolean(), nullable=False),
        sa.Column("local_path", sa.String(1024), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email_id"], ["emails.email_id"]),
        sa.PrimaryKeyConstraint("attachment_id"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("upstream_run_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("model_version", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(128), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_agent_runs_agent_name", "agent_runs", ["agent_name"])
    op.create_index("ix_agent_runs_email_id", "agent_runs", ["email_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_trace_id", "agent_runs", ["trace_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])

    op.create_table(
        "classifier_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("urgency_score", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("sender_role", sa.String(128), nullable=False),
        sa.Column("named_entities", sa.JSON(), nullable=True),
        sa.Column("time_expressions", sa.JSON(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_classifier_results_email_id", "classifier_results", ["email_id"])
    op.create_index("ix_classifier_results_is_current", "classifier_results", ["is_current"])
    op.create_index("ix_classifier_results_trace_id", "classifier_results", ["trace_id"])
    op.create_index("ix_classifier_results_user_id", "classifier_results", ["user_id"])

    op.create_table(
        "category_definitions",
        sa.Column("category_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("category_name", sa.String(128), nullable=False),
        sa.Column("category_description", sa.Text(), nullable=False),
        sa.Column("created_from_email_id", sa.String(64), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("category_id"),
    )
    op.create_index("ix_category_definitions_category_name", "category_definitions", ["category_name"])
    op.create_index("ix_category_definitions_user_id", "category_definitions", ["user_id"])

    op.create_table(
        "attachment_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("attachment_id", sa.String(64), nullable=False),
        sa.Column("doc_type", sa.String(128), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=True),
        sa.Column("named_entities", sa.JSON(), nullable=True),
        sa.Column("time_expressions", sa.JSON(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachment_results_attachment_id", "attachment_results", ["attachment_id"])
    op.create_index("ix_attachment_results_email_id", "attachment_results", ["email_id"])
    op.create_index("ix_attachment_results_is_current", "attachment_results", ["is_current"])
    op.create_index("ix_attachment_results_trace_id", "attachment_results", ["trace_id"])
    op.create_index("ix_attachment_results_user_id", "attachment_results", ["user_id"])

    op.create_table(
        "relationship_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("person_email", sa.String(255), nullable=False),
        sa.Column("person_name", sa.String(255), nullable=True),
        sa.Column("person_role", sa.String(128), nullable=True),
        sa.Column("organisation_name", sa.String(255), nullable=True),
        sa.Column("organisation_domain", sa.String(255), nullable=True),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("signal_weight", sa.Float(), nullable=False),
        sa.Column("observed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relationship_observations_email_id", "relationship_observations", ["email_id"])
    op.create_index("ix_relationship_observations_person_email", "relationship_observations", ["person_email"])
    op.create_index("ix_relationship_observations_trace_id", "relationship_observations", ["trace_id"])
    op.create_index("ix_relationship_observations_user_id", "relationship_observations", ["user_id"])

    op.create_table(
        "schedule_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("start_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timezone", sa.String(64), nullable=False),
        sa.Column("is_all_day", sa.Boolean(), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("attendees", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("conflict_score", sa.Float(), nullable=False),
        sa.Column("recommendation_rank", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("show_as", sa.String(64), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("outlook_event_id", sa.String(255), nullable=True),
        sa.Column("outlook_weblink", sa.String(1024), nullable=True),
        sa.Column("write_status", sa.String(32), nullable=False),
        sa.Column("last_write_error", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedule_candidates_candidate_id", "schedule_candidates", ["candidate_id"])
    op.create_index("ix_schedule_candidates_email_id", "schedule_candidates", ["email_id"])
    op.create_index("ix_schedule_candidates_is_current", "schedule_candidates", ["is_current"])
    op.create_index("ix_schedule_candidates_trace_id", "schedule_candidates", ["trace_id"])
    op.create_index("ix_schedule_candidates_user_id", "schedule_candidates", ["user_id"])

    op.create_table(
        "reply_suggestions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("reply_required", sa.Boolean(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("tone_templates", sa.JSON(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reply_suggestions_email_id", "reply_suggestions", ["email_id"])
    op.create_index("ix_reply_suggestions_is_current", "reply_suggestions", ["is_current"])
    op.create_index("ix_reply_suggestions_trace_id", "reply_suggestions", ["trace_id"])
    op.create_index("ix_reply_suggestions_user_id", "reply_suggestions", ["user_id"])

    op.create_table(
        "user_mailbox_accounts",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("graph_user_id", sa.String(128), nullable=False),
        sa.Column("token_blob", sa.JSON(), nullable=True),
        sa.Column("token_expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_user_mailbox_accounts_graph_user_id", "user_mailbox_accounts", ["graph_user_id"])

    op.create_table(
        "user_mailbox_state",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("mailbox_connected", sa.Boolean(), nullable=False),
        sa.Column("bootstrap_status", sa.String(32), nullable=False),
        sa.Column("bootstrap_started_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bootstrap_completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bootstrap_error", sa.Text(), nullable=True),
        sa.Column("polling_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_poll_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inbox_delta_token", sa.Text(), nullable=True),
        sa.Column("sent_delta_token", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_user_mailbox_state_bootstrap_status", "user_mailbox_state", ["bootstrap_status"])

    op.create_table(
        "user_writing_profiles",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("preferred_language", sa.String(32), nullable=True),
        sa.Column("tone_profile", sa.String(64), nullable=True),
        sa.Column("avg_length_bucket", sa.String(32), nullable=True),
        sa.Column("greeting_patterns", sa.JSON(), nullable=True),
        sa.Column("closing_patterns", sa.JSON(), nullable=True),
        sa.Column("signature_blocks", sa.JSON(), nullable=True),
        sa.Column("cta_patterns", sa.JSON(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("profile_payload", sa.JSON(), nullable=True),
        sa.Column("last_profiled_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("sync_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.Column("items_processed", sa.Integer(), nullable=False),
        sa.Column("items_failed", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])
    op.create_index("ix_sync_runs_sync_type", "sync_runs", ["sync_type"])
    op.create_index("ix_sync_runs_user_id", "sync_runs", ["user_id"])

    op.create_table(
        "reply_draft_writes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reply_suggestion_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("policy_name", sa.String(128), nullable=False),
        sa.Column("draft_status", sa.String(32), nullable=False),
        sa.Column("outlook_draft_id", sa.String(255), nullable=True),
        sa.Column("outlook_web_link", sa.String(1024), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reply_draft_writes_draft_status", "reply_draft_writes", ["draft_status"])
    op.create_index("ix_reply_draft_writes_email_id", "reply_draft_writes", ["email_id"])
    op.create_index("ix_reply_draft_writes_reply_suggestion_id", "reply_draft_writes", ["reply_suggestion_id"])
    op.create_index("ix_reply_draft_writes_user_id", "reply_draft_writes", ["user_id"])

    op.create_table(
        "system_leases",
        sa.Column("lock_name", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("locked_until_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lock_name"),
    )


def downgrade() -> None:
    op.drop_table("system_leases")
    op.drop_table("reply_draft_writes")
    op.drop_table("sync_runs")
    op.drop_table("user_writing_profiles")
    op.drop_table("user_mailbox_state")
    op.drop_table("user_mailbox_accounts")
    op.drop_table("reply_suggestions")
    op.drop_table("schedule_candidates")
    op.drop_table("relationship_observations")
    op.drop_table("attachment_results")
    op.drop_table("category_definitions")
    op.drop_table("classifier_results")
    op.drop_table("agent_runs")
    op.drop_table("attachments")
    op.drop_table("email_recipients")
    op.drop_table("emails")
    op.drop_table("users")
