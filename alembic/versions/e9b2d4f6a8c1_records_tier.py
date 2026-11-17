"""records.tier and records.tier_pos — 티어리스트에 직접 놓은 줄과 순서

Revision ID: e9b2d4f6a8c1
Revises: d3a5c8f1e7b4
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9b2d4f6a8c1'
down_revision: Union[str, Sequence[str], None] = 'd3a5c8f1e7b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 기록은 직접 놓은 적 없음(NULL) — 별점으로 자동 배치됨.
    op.add_column('records', sa.Column('tier', sa.String(), nullable=True))
    op.add_column('records', sa.Column('tier_pos', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('records', 'tier_pos')
    op.drop_column('records', 'tier')
