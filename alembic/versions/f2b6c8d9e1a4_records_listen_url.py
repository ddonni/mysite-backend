"""records.listen_url — the YouTube link a music record plays

Revision ID: f2b6c8d9e1a4
Revises: d4a8f1c2e693
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b6c8d9e1a4'
down_revision: Union[str, Sequence[str], None] = 'd4a8f1c2e693'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 기록은 링크 없음(NULL) — 틀 버튼이 안 뜨고, 수정하기에서 붙여 넣을 수 있음.
    op.add_column('records', sa.Column('listen_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('records', 'listen_url')
