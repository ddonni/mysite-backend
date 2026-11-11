"""wardrobe — records.attrs (per-category extra fields) and rooms.wardrobe_public

Revision ID: a7c3e5f9b2d1
Revises: f2b6c8d9e1a4
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e5f9b2d1'
down_revision: Union[str, Sequence[str], None] = 'f2b6c8d9e1a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 기록은 추가 칸 없음(NULL), 기존 방은 옷장 비공개(주인만 봄).
    op.add_column('records', sa.Column('attrs', sa.JSON(), nullable=True))
    op.add_column('rooms', sa.Column('wardrobe_public', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('rooms', 'wardrobe_public')
    op.drop_column('records', 'attrs')
