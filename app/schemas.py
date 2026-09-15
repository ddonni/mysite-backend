from typing import List, Literal, Optional

from pydantic import BaseModel


class Stroke(BaseModel):
    """One pen or eraser stroke, in the same compact shape the canvas
    stores it in: normalized (0..1) points so it renders correctly at any
    screen size, a color (None for an eraser stroke), a width, and an id
    used to de-duplicate when a save races with another viewer's."""

    id: str
    color: Optional[str] = None
    eraser: bool = False
    width: float
    author: Optional[str] = None
    points: List[List[float]]


class StrokesIn(BaseModel):
    strokes: List[Stroke]


class MetaOut(BaseModel):
    count: int


class PageOut(BaseModel):
    page_number: int
    strokes: List[Stroke]


class NewPageOut(BaseModel):
    page_number: int
    count: int


class DeleteOut(BaseModel):
    count: int


class RecordIn(BaseModel):
    """One 'watched/read it' log entry. photo_url comes from POST
    /api/uploads — the client uploads the photo first, then sends the
    returned URL along with the rest of the fields."""

    cat: Literal["book", "anime", "movie"]
    title: str
    creator: Optional[str] = None
    rating: float = 0
    memo: Optional[str] = None
    photo_url: Optional[str] = None


class RecordOut(RecordIn):
    id: int
    date: str


class UploadOut(BaseModel):
    url: str
