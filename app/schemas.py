from typing import List, Optional

from pydantic import BaseModel


class Stroke(BaseModel):
    """One pen or eraser stroke, in the same compact shape the canvas
    stores it in: normalized (0..1) points so it renders correctly at any
    screen size, a color (None for an eraser stroke), a width, and an id
    used to de-duplicate when a save races with another viewer's."""

    id: str
    c: Optional[str] = None
    e: bool = False
    s: float
    by: Optional[str] = None
    p: List[List[float]]


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
