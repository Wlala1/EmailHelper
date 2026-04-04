"""add preference_vector to user_writing_profiles

Revision ID: 003
Revises: 002
Create Date: 2026-04-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_writing_profiles",
        sa.Column("preference_vector", sa.JSON(), nullable=True),
    )
    op.add_column(
        "user_writing_profiles",
        sa.Column("preference_vector_updated_at_utc", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_writing_profiles", "preference_vector_updated_at_utc")
    op.drop_column("user_writing_profiles", "preference_vector")
