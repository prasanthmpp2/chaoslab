"""Persist CI pipeline state in PostgreSQL.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models.base import JSONType

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 0001 uses current model metadata, so fresh installs may already have the table.
    if inspect(bind).has_table("pipeline_executions"):
        return
    op.create_table(
        "pipeline_executions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pipeline_status_created", "pipeline_executions", ["status", "created_at"])
    op.create_index("ix_pipeline_executions_status", "pipeline_executions", ["status"])


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("pipeline_executions"):
        op.drop_table("pipeline_executions")
