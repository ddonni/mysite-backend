from sqlalchemy import JSON, Column, DateTime, Integer, func

from .database import Base


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
