"""Widen attachment_id columns from VARCHAR(64) to VARCHAR(512).

Revision ID: 006
Revises: 005
Create Date: 2026-04-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not enforce VARCHAR length and does not support ALTER COLUMN.
    # batch_alter_table uses a copy-and-move strategy that works on both dialects.
    with op.batch_alter_table("attachments") as batch_op:
        batch_op.alter_column(
            "attachment_id",
            existing_type=sa.String(64),
            type_=sa.String(512),
            existing_nullable=False,
        )
    with op.batch_alter_table("attachment_results") as batch_op:
        batch_op.alter_column(
            "attachment_id",
            existing_type=sa.String(64),
            type_=sa.String(512),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("attachment_results") as batch_op:
        batch_op.alter_column(
            "attachment_id",
            existing_type=sa.String(512),
            type_=sa.String(64),
            existing_nullable=False,
        )
    with op.batch_alter_table("attachments") as batch_op:
        batch_op.alter_column(
            "attachment_id",
            existing_type=sa.String(512),
            type_=sa.String(64),
            existing_nullable=False,
        )
