"""remove the food category (냉장고) — delete food records, title NOT NULL again

Revision ID: c1533fac1a81
Revises: 5e54f3af75ad
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1533fac1a81'
down_revision: Union[str, Sequence[str], None] = '5e54f3af75ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 음식 카테고리 자체를 없애서, API가 더 이상 cat='food' 기록을 응답
    # 스키마(RecordOut)로 표현할 수 없음 — 남겨두면 목록 조회가 500이 남.
    # 되돌릴 수 없는 삭제이고, S3에 올라간 사진 파일은 여기서 지우지 않음.
    op.execute("DELETE FROM records WHERE cat = 'food'")
    # 제목 없는 기록은 food뿐이었지만, 혹시 남아 있으면 NOT NULL 복구가
    # 실패하니 빈 문자열로 채워 둠.
    op.execute("UPDATE records SET title = '' WHERE title IS NULL")
    op.alter_column('records', 'title', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    # 스키마만 되돌림 — 지운 food 기록은 복구되지 않음.
    op.alter_column('records', 'title', existing_type=sa.String(), nullable=True)
