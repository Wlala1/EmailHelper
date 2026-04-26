"""Make classifier_results.sender_role nullable.

Revision ID: 010
Revises: 009
Create Date: 2026-04-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "classifier_results",
        "sender_role",
        existing_type=sa.String(128),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE classifier_results SET sender_role = '' WHERE sender_role IS NULL")
    op.alter_column(
        "classifier_results",
        "sender_role",
        existing_type=sa.String(128),
        nullable=False,
    )
