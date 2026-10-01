"""records.playing — the song the owner picked to spin on the lobby turntable

Revision ID: a3f9c2d71b84
Revises: c1533fac1a81
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f9c2d71b84'
down_revision: Union[str, Sequence[str], None] = 'c1533fac1a81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 기록은 전부 "직접 고른 재생 곡 아님"으로 채움 — 그러면 로비는
    # 지금까지처럼 최애음악 첫 번째(없으면 최신 곡)를 돌림.
    op.add_column('records', sa.Column('playing', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('records', 'playing')
