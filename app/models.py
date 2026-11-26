from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, false, func
from sqlalchemy.orm import relationship

from .database import Base


class Room(Base):
    """One person's space — a sketchbook plus a library, both scoped to
    this room's id. `code` is the short, shareable string that goes in
    URLs and that visitors type in to look a room up (read-only for them).
    The room's token is the long secret only the owner's browser holds (in
    localStorage) and sends back on writes to prove it's really them —
    see the require_owner dependency in main.py. Only its SHA-256 hash
    (`token_hash`) is stored here, never the token itself (crud.hash_token).
    """

    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False)
    # 로비 3D 씬의 색 팔레트 프리셋 이름(app.schemas.ROOM_THEMES) — 방
    # 주인이 고름, 코드만 있으면 누구나 GET으로 볼 수 있는 비밀 아닌
    # 값이라 owner_token 없이도 read_room에서 그대로 돌려줌.
    theme = Column(String, nullable=False, default="wood")
    # 로비 제목에 "누구 방인지"로 보여줄 방 이름(최대 20자, 방 주인이 지음).
    # 테마처럼 코드만 있으면 누구나 볼 수 있는 값이고, 안 지었으면 NULL.
    name = Column(String, nullable=True)
    # 이 방에 연결해둔 구글 계정의 안정적인 사용자 id(ID 토큰의 sub
    # 클레임). 로그인 시스템이 있는 건 아니고, 브라우저 localStorage가
    # 지워지거나 새 기기로 옮길 때 token을 다시 찾아오기 위한 용도라서
    # 하나의 구글 계정은 최대 한 방에만 연결됨(unique). 대부분은 NULL.
    google_sub = Column(String, unique=True, nullable=True, index=True)
    # 위 계정의 이메일 — 화면에 "어떤 계정이 연동됐는지" 보여주기 위한
    # 표시용 값일 뿐, 조회/인증 어디에도 이 값 자체로는 안 씀(sub가 진짜 키).
    google_email = Column(String, nullable=True)
    # "방 둘러보기"(GET /api/rooms) 목록에 이 방을 올릴지 — 방 주인이 고름.
    # 꺼져 있어도 코드를 아는 사람은 지금처럼 들어올 수 있음(목록에만 안 뜸).
    # 처음엔 다들 비공개 — 모르는 사이에 남들 목록에 뜨지 않게.
    is_public = Column(Boolean, nullable=False, default=False, server_default=false())
    # 프로필 — 첫 화면 위 줄(이름 왼쪽)과 기록 둘러보기 카드에 보임. 이름처럼 코드만 있으면
    # 누구나 볼 수 있는 값. 사진은 POST .../uploads로 올린 우리 S3 주소만(schemas.RoomAvatarIn),
    # 없으면 NULL(프론트가 기본 실루엣). bio는 기록을 소개하는 한 줄(최대 40자).
    avatar_url = Column(String, nullable=True)
    bio = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    pages = relationship("Page", back_populates="room", cascade="all, delete-orphan")
    records = relationship("Record", back_populates="room", cascade="all, delete-orphan")


class Record(Base):
    """One 'watched/read it' log entry (book, anime, movie, or music).

    photo_url points at an object in S3 — the browser uploads the photo
    directly through the API, which forwards it to S3 and stores only the
    resulting URL here, instead of putting image bytes in the database.
    """

    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    cat = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    creator = Column(String, nullable=True)
    rating = Column(Float, nullable=False, default=0)
    memo = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    date = Column(String, nullable=False)
    # "인생작" 표시(화면에선 인생책/인생애니/인생영화/최애음악) — 방 안에서
    # 카테고리마다 최대 3개(crud.FEATURED_PER_CAT)까지 켤 수 있고, 로비의 각
    # 카테고리 가구 맨 앞자리에 걸림.
    featured = Column(Boolean, nullable=False, default=False)
    # 로비 턴테이블에서 돌릴 곡으로 직접 고른 음악 — 방마다 최대 한 곡
    # (crud.set_playing이 다른 곡을 끔). 아무것도 안 고르면 프론트(showcase.js의
    # pickMusic)가 최애음악 첫 번째, 없으면 가장 최근 곡을 돌림.
    playing = Column(Boolean, nullable=False, default=False, server_default=false())
    # 음악을 실제로 틀 유튜브 주소(schemas.listen_url이 유튜브 주소만 받음) — 검색 결과에서
    # 고르면 앨범 재생목록 + 몇 번째 곡(music_search.listen_url), 직접 붙여 넣으면 영상 주소.
    # 프론트는 유튜브 임베드 플레이어로 틂(js/shared/youtube.js). 없으면 NULL.
    listen_url = Column(String, nullable=True)
    # 보는 중·읽는 중(2026-11-17) — 아직 다 안 본 작품. 음악은 없음(늘 False).
    in_progress = Column(Boolean, nullable=False, default=False, server_default=false())
    # 세부 기록(선택, 2026-11-17) — {"scores": {"story": 4.5, ...}, "genres": ["판타지", ...]}.
    # 카테고리마다 받는 칸이 다름(schemas.DETAIL_ASPECTS·DETAIL_GENRES). 음악은 없음. 안 썼으면 NULL.
    details = Column(JSON, nullable=True)
    # 티어리스트(2026-11-17) — 방 주인이 직접 놓은 줄("SS"…"F", 미배치로 뺐으면 "-")과 그 줄 안의
    # 순서. NULL이면 직접 놓지 않은 것 — 프론트가 별점으로 자동 배치함(js/tier/board.js의 tierFromRating).
    tier = Column(String, nullable=True)
    tier_pos = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    room = relationship("Room", back_populates="records")


class GuestbookEntry(Base):
    """방명록 글 하나 — room(방명록이 있는 방)에 author_room(쓴 사람의 방)이 남김.
    익명이어도 author_room_id는 저장함(본인·방 주인이 지울 수 있게). app/social_routes.py."""

    __tablename__ = "guestbook_entries"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    author_room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    message = Column(String, nullable=False)
    anonymous = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    author = relationship("Room", foreign_keys=[author_room_id])


class RoomLike(Base):
    """좋아요 하나 — liker_room(누른 사람의 방)이 room에. 한 방에 한 번만(유니크)."""

    __tablename__ = "room_likes"
    __table_args__ = (UniqueConstraint("room_id", "liker_room_id", name="uq_room_likes_room_liker"),)

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    liker_room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RecordLike(Base):
    """카드(기록 하나)에 누른 공감 — liker_room(누른 사람의 방)이 record에. 한 번만(유니크).
    기록을 지우면 같이 지움(crud.delete_record)."""

    __tablename__ = "record_likes"
    __table_args__ = (UniqueConstraint("record_id", "liker_room_id", name="uq_record_likes_record_liker"),)

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("records.id"), nullable=False, index=True)
    liker_room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    """사이트 어디서나 "의견 보내기"로 받은 피드백 한 건. 방과 달리 누구나
    (토큰 없이) 보낼 수 있고, 로그인이 없으니 누가 보냈는지는 본인이 적은
    이름(선택)뿐. 읽는 건 운영자만(GET /api/admin/feedback, 구글 로그인).
    page/room_code는 보낸 사람이 어느 화면·어느 방을 보다가 썼는지(맥락용)."""

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    message = Column(String, nullable=False)
    name = Column(String, nullable=True)
    page = Column(String, nullable=True)
    room_code = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Page(Base):
    """One sketchbook page.

    page_number is the human-facing, 1-based page number the frontend
    navigates by. It's kept contiguous (1..count, no gaps) *within a
    room* — when a page is deleted, every later page's number in that
    same room is shifted down by one instead of leaving a hole, so
    "page 3 of 5" always means what it says. Different rooms each start
    their own 1..count sequence, which is why the uniqueness constraint
    is on (room_id, page_number) rather than on page_number alone.
    """

    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("room_id", "page_number", name="uq_pages_room_page_number"),)

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False, index=True)
    strokes = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    room = relationship("Room", back_populates="pages")
