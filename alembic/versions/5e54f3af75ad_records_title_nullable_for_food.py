"""records.title nullable (food/냉장고 entries don't need a title)

Revision ID: 5e54f3af75ad
Revises: e7c3ca122668
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e54f3af75ad'
down_revision: Union[str, Sequence[str], None] = 'e7c3ca122668'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('records', 'title', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # 되돌릴 땐 제목 없는 food 기록이 이미 있을 수 있어서, NOT NULL을
    # 복구하기 전에 빈 문자열로 채워 제약 위반을 막음.
    op.execute("UPDATE records SET title = '' WHERE title IS NULL")
    op.alter_column('records', 'title', existing_type=sa.String(), nullable=False)
