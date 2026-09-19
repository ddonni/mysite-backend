from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
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
