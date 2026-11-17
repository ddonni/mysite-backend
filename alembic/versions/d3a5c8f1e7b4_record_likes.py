"""record_likes — 카드(기록 하나)마다 누르는 공감

Revision ID: d3a5c8f1e7b4
Revises: c7f1a9e3d5b2
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3a5c8f1e7b4'
down_revision: Union[str, Sequence[str], None] = 'c7f1a9e3d5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'record_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('record_id', sa.Integer(), sa.ForeignKey('records.id'), nullable=False),
        sa.Column('liker_room_id', sa.Integer(), sa.ForeignKey('rooms.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('record_id', 'liker_room_id', name='uq_record_likes_record_liker'),
    )
    op.create_index('ix_record_likes_id', 'record_likes', ['id'])
    op.create_index('ix_record_likes_record_id', 'record_likes', ['record_id'])
    op.create_index('ix_record_likes_liker_room_id', 'record_likes', ['liker_room_id'])


def downgrade() -> None:
    op.drop_table('record_likes')
