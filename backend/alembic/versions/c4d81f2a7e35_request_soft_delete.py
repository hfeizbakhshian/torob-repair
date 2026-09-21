"""request soft delete

Revision ID: c4d81f2a7e35
Revises: b320ff54f5b0
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4d81f2a7e35'
down_revision: str | None = 'b320ff54f5b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_requests_deleted_at", "requests", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_requests_deleted_at", table_name="requests")
    op.drop_column("requests", "deleted_at")
