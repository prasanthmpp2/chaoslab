"""Persist run recovery duration and scorecard.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models.base import JSONType

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("experiment_runs")}
    if "recovery_duration_seconds" not in columns:
        op.add_column("experiment_runs", sa.Column("recovery_duration_seconds", sa.Float(), nullable=True))
    if "scorecard" not in columns:
        op.add_column("experiment_runs", sa.Column("scorecard", JSONType, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("experiment_runs")}
    if "scorecard" in columns:
        op.drop_column("experiment_runs", "scorecard")
    if "recovery_duration_seconds" in columns:
        op.drop_column("experiment_runs", "recovery_duration_seconds")
