"""add category suggestions table

Revision ID: 004
Revises: 003
Create Date: 2026-04-05

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_suggestions",
        sa.Column("suggestion_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("category_name", sa.String(128), nullable=False),
        sa.Column("category_description", sa.Text(), nullable=False),
        sa.Column("supporting_email_ids", sa.JSON(), nullable=True),
        sa.Column("supporting_subjects", sa.JSON(), nullable=True),
        sa.Column("rationale_keywords", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("process_limit", sa.Integer(), nullable=False),
        sa.Column("created_from_email_id", sa.String(64), nullable=True),
        sa.Column("promoted_category_id", sa.String(64), nullable=True),
        sa.Column("decided_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("suggestion_id"),
    )
    op.create_index("ix_category_suggestions_category_name", "category_suggestions", ["category_name"])
    op.create_index("ix_category_suggestions_status", "category_suggestions", ["status"])
    op.create_index("ix_category_suggestions_user_id", "category_suggestions", ["user_id"])


def downgrade() -> None:
    op.drop_table("category_suggestions")
