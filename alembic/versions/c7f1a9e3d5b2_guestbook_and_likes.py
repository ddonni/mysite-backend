"""guestbook_entries and room_likes — 방명록과 좋아요

Revision ID: c7f1a9e3d5b2
Revises: b4e8d2a6c913
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f1a9e3d5b2'
down_revision: Union[str, Sequence[str], None] = 'b4e8d2a6c913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'guestbook_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('rooms.id'), nullable=False),
        sa.Column('author_room_id', sa.Integer(), sa.ForeignKey('rooms.id'), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('anonymous', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_guestbook_entries_id', 'guestbook_entries', ['id'])
    op.create_index('ix_guestbook_entries_room_id', 'guestbook_entries', ['room_id'])
    op.create_index('ix_guestbook_entries_author_room_id', 'guestbook_entries', ['author_room_id'])
    op.create_table(
        'room_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('rooms.id'), nullable=False),
        sa.Column('liker_room_id', sa.Integer(), sa.ForeignKey('rooms.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('room_id', 'liker_room_id', name='uq_room_likes_room_liker'),
    )
    op.create_index('ix_room_likes_id', 'room_likes', ['id'])
    op.create_index('ix_room_likes_room_id', 'room_likes', ['room_id'])
    op.create_index('ix_room_likes_liker_room_id', 'room_likes', ['liker_room_id'])


def downgrade() -> None:
    op.drop_table('room_likes')
    op.drop_table('guestbook_entries')
