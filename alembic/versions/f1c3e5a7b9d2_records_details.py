"""records.details — 세부 기록(요소별 별점·장르)

Revision ID: f1c3e5a7b9d2
Revises: e9b2d4f6a8c1
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1c3e5a7b9d2'
down_revision: Union[str, Sequence[str], None] = 'e9b2d4f6a8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('records', sa.Column('details', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('records', 'details')
