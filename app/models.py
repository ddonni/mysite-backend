from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, func

from .database import Base


class Record(Base):
    """One 'watched/read it' log entry (book, anime, or movie).

    photo_url points at an object in S3 — the browser uploads the photo
    directly through the API, which forwards it to S3 and stores only the
    resulting URL here, instead of putting image bytes in the database.
    """

    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    cat = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    creator = Column(String, nullable=True)
    rating = Column(Float, nullable=False, default=0)
    memo = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    date = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Page(Base):
    """One sketchbook page.

    page_number is the human-facing, 1-based page number the frontend
    navigates by. It's kept contiguous (1..count, no gaps) — when a page is
    deleted, every later page's number is shifted down by one instead of
    leaving a hole, so "page 3 of 5" always means what it says.
    """

    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)
    page_number = Column(Integer, unique=True, nullable=False, index=True)
    strokes = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
