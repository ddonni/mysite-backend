"""rooms.is_public — whether the owner lists the room in "방 둘러보기"

Revision ID: b7d41e9c0a52
Revises: a3f9c2d71b84
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d41e9c0a52'
down_revision: Union[str, Sequence[str], None] = 'a3f9c2d71b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 방은 전부 비공개로 시작 — 방 주인이 직접 켜야 목록에 뜸.
    op.add_column('rooms', sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('rooms', 'is_public')
