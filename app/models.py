from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .database import Base


class Room(Base):
    """One person's space — a sketchbook plus a library, both scoped to
    this room's id. `code` is the short, shareable string that goes in
    URLs and that visitors type in to look a room up (read-only for them).
    `token` is the long secret only the owner's browser holds (in
    localStorage) and sends back on writes to prove it's really them —
    see the require_owner dependency in main.py.
    """

    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    token = Column(String, unique=True, nullable=False)
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
    # 방마다 딱 하나만 "인생작품"으로 켜져 있음(로비 액자에 걸림) —
    # crud.set_featured가 새로 켤 때 같은 방의 나머지를 자동으로 끔.
    featured = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    room = relationship("Room", back_populates="records")


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
