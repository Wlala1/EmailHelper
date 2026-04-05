"""Add partial unique indexes for is_current and unique constraint on category_definitions.

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
    bind = op.get_bind()

    # ── Fix 1: partial unique indexes for is_current soft-delete tables ─────
    # Only PostgreSQL supports partial (conditional) unique indexes.
    # SQLite dev environments skip these — the constraint is enforced in prod.
    if bind.dialect.name == "postgresql":
        op.create_index(
            "uq_classifier_results_email_current",
            "classifier_results",
            ["email_id"],
            unique=True,
            postgresql_where=sa.text("is_current = TRUE"),
        )
        op.create_index(
            "uq_reply_suggestions_email_current",
            "reply_suggestions",
            ["email_id"],
            unique=True,
            postgresql_where=sa.text("is_current = TRUE"),
        )
        op.create_index(
            "uq_schedule_candidates_email_current",
            "schedule_candidates",
            ["email_id"],
            unique=True,
            postgresql_where=sa.text("is_current = TRUE"),
        )
        op.create_index(
            "uq_attachment_results_attachment_current",
            "attachment_results",
            ["attachment_id"],
            unique=True,
            postgresql_where=sa.text("is_current = TRUE"),
        )

    # ── Fix 3: unique constraint on (user_id, category_name) ─────────────────
    op.create_unique_constraint(
        "uq_category_definitions_user_name",
        "category_definitions",
        ["user_id", "category_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_category_definitions_user_name",
        "category_definitions",
        type_="unique",
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("uq_attachment_results_attachment_current", table_name="attachment_results")
        op.drop_index("uq_schedule_candidates_email_current", table_name="schedule_candidates")
        op.drop_index("uq_reply_suggestions_email_current", table_name="reply_suggestions")
        op.drop_index("uq_classifier_results_email_current", table_name="classifier_results")
