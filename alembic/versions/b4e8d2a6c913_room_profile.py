"""rooms.avatar_url and rooms.bio — the profile photo and one-line intro

Revision ID: b4e8d2a6c913
Revises: f2b6c8d9e1a4
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e8d2a6c913'
down_revision: Union[str, Sequence[str], None] = 'f2b6c8d9e1a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 방은 사진·소개 없음(NULL) — 프론트가 기본 실루엣을 보여 줌.
    op.add_column('rooms', sa.Column('avatar_url', sa.String(), nullable=True))
    op.add_column('rooms', sa.Column('bio', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('rooms', 'bio')
    op.drop_column('rooms', 'avatar_url')
