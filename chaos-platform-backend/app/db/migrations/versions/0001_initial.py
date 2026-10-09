"""initial schema

Revision ID: 0001
Revises:
NOTE: this migration creates the tables from the model metadata as of the first release
(a frozen snapshot is preferable long term; generate later revisions with
`alembic revision --autogenerate`). It has NOT been executed in the authoring sandbox.
"""
from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
