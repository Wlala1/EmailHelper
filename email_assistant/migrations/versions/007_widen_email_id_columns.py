"""Widen email_id and related columns from VARCHAR(64) to VARCHAR(255).

Revision ID: 007
Revises: 006
Create Date: 2026-04-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

_EMAIL_ID_COLS = [
    ("emails", "email_id"),
    ("email_recipients", "email_id"),
    ("attachments", "email_id"),
    ("agent_runs", "email_id"),
    ("classifier_results", "email_id"),
    ("attachment_results", "email_id"),
    ("relationship_observations", "email_id"),
    ("schedule_candidates", "email_id"),
    ("reply_suggestions", "email_id"),
    ("user_feedback_events", "email_id"),
    ("reply_draft_writes", "email_id"),
    ("category_definitions", "created_from_email_id"),
    ("category_suggestions", "created_from_email_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite does not enforce VARCHAR length; skip the no-op ALTER.
        return
    for table, column in _EMAIL_ID_COLS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR(255)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for table, column in reversed(_EMAIL_ID_COLS):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR(64)")
