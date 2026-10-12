"""Persist request and response evidence for HTTP probes.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models.base import JSONType

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table("probe_calls"):
        op.create_table(
            "probe_calls",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True,
                      autoincrement=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("experiment_runs.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("probe_name", sa.String(64), nullable=False),
            sa.Column("phase", sa.String(16), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("duration_ms", sa.Float(), nullable=False),
            sa.Column("ok", sa.Boolean(), nullable=False),
            sa.Column("request", JSONType, nullable=False),
            sa.Column("response", JSONType, nullable=False),
        )
        op.create_index("ix_probe_calls_run_id", "probe_calls", ["run_id"])
        op.create_index("ix_probe_calls_run_phase_time", "probe_calls", ["run_id", "phase", "captured_at"])


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("probe_calls"):
        op.drop_table("probe_calls")
