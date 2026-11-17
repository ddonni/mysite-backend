"""records.in_progress — 보는 중·읽는 중

Revision ID: a2d4f6b8c0e3
Revises: f1c3e5a7b9d2
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2d4f6b8c0e3'
down_revision: Union[str, Sequence[str], None] = 'f1c3e5a7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 기록은 다 본 것(False).
    op.add_column('records', sa.Column('in_progress', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('records', 'in_progress')
