"""records.attrs·photos — 옷 기록의 추가 칸, 일기의 사진 여러 장

Revision ID: c5e7a9b1d3f6
Revises: a2d4f6b8c0e3
Create Date: 2026-11-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e7a9b1d3f6'
down_revision: Union[str, Sequence[str], None] = 'a2d4f6b8c0e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 옷(cat="clothes")의 추가 칸, 일기(cat="diary")의 사진 목록 — 기존 기록은 전부 NULL.
    op.add_column('records', sa.Column('attrs', sa.JSON(), nullable=True))
    op.add_column('records', sa.Column('photos', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('records', 'photos')
    op.drop_column('records', 'attrs')
