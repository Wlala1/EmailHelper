"""Add proposed_category_name to classifier_results.

Revision ID: 009
Revises: 008
Create Date: 2026-04-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "classifier_results",
        sa.Column("proposed_category_name", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_classifier_results_proposed_category_name",
        "classifier_results",
        ["proposed_category_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_classifier_results_proposed_category_name", table_name="classifier_results")
    op.drop_column("classifier_results", "proposed_category_name")
