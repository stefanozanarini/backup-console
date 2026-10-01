"""NAS nella stessa rete

Revision ID: 4be1c8d2a7f3
Revises: 92606d166169
Create Date: 2026-10-01 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '4be1c8d2a7f3'
down_revision: str | None = '92606d166169'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('nas_neighbors', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'nas_neighbors')
