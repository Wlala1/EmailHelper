"""add user_feedback_events table

Revision ID: 002
Revises: 001
Create Date: 2026-04-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_feedback_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("email_id", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("feedback_signal", sa.String(32), nullable=False),
        sa.Column("feedback_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_feedback_events_user_id", "user_feedback_events", ["user_id"])
    op.create_index("ix_user_feedback_events_email_id", "user_feedback_events", ["email_id"])
    op.create_index("ix_user_feedback_events_target_type", "user_feedback_events", ["target_type"])
    op.create_index("ix_user_feedback_events_feedback_signal", "user_feedback_events", ["feedback_signal"])


def downgrade() -> None:
    op.drop_table("user_feedback_events")
