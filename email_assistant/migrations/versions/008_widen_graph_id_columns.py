"""Widen Microsoft Graph ID columns from VARCHAR(255) to TEXT.

Graph IDs (internet_message_id, conversation_id, graph_message_id,
graph_immutable_id, graph_parent_folder_id) regularly exceed 255 chars.

Revision ID: 008
Revises: 007
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_GRAPH_ID_COLS = [
    ("emails", "graph_message_id"),
    ("emails", "graph_immutable_id"),
    ("emails", "internet_message_id"),
    ("emails", "conversation_id"),
    ("emails", "graph_parent_folder_id"),
    ("attachments", "graph_attachment_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for table, column in _GRAPH_ID_COLS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TEXT")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for table, column in reversed(_GRAPH_ID_COLS):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR(255)")
