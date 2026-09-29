"""risultato strutturato dei comandi

Revision ID: b7c1d2e3f4a5
Revises: aa30f8fbf9ca
Create Date: 2026-09-29 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "aa30f8fbf9ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("commands", sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("commands", "result")
