"""rooms.token → rooms.token_hash — 방 token을 평문 대신 SHA-256 해시로만 저장

DB나 S3 백업이 새도 방 token(수정 권한)이 같이 새지 않게 한다. 기존 방은
지금 token의 해시로 옮겨서, 브라우저가 들고 있는 token은 그대로 통한다.

되돌릴 수 없는 변경: 해시에서 token을 다시 만들 수 없어서 downgrade가 없다.
이 리비전 전으로 돌아가야 하면 배포 전 백업(pg_dump)으로 복원한다.

Revision ID: b9e1c3d5f7a2
Revises: a2d4f6b8c0e3
Create Date: 2026-11-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9e1c3d5f7a2'
down_revision: Union[str, Sequence[str], None] = 'a2d4f6b8c0e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rooms', sa.Column('token_hash', sa.String(), nullable=True))
    # Python의 hashlib.sha256(token.encode()).hexdigest()와 같은 값(crud.hash_token).
    op.execute("UPDATE rooms SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex')")
    op.alter_column('rooms', 'token_hash', nullable=False)
    op.create_unique_constraint('rooms_token_hash_key', 'rooms', ['token_hash'])
    # 평문 칸을 지움 — 이 칸의 UNIQUE 제약도 같이 없어진다.
    op.drop_column('rooms', 'token')


def downgrade() -> None:
    raise RuntimeError(
        "rooms.token_hash cannot be turned back into plain tokens; "
        "restore the pre-deploy pg_dump backup instead"
    )
