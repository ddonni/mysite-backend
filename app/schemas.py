from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, StringConstraints


class RoomOut(BaseModel):
    """Returned once, right after a room is created. `token` is the
    secret that proves ownership on future writes — the client stores it
    (localStorage) and never shows it on screen. `code` is the short,
    shareable id that goes in URLs and that visitors type in."""

    code: str
    token: str


# The lobby's 3D scene picks its color palette from this — keep in sync
# with THEMES in mysite's js/lobby/scene.js.
ROOM_THEMES = ("wood", "night", "pastel")


class RoomThemeIn(BaseModel):
    theme: Literal[ROOM_THEMES]


class RoomNameIn(BaseModel):
    """PUT .../name — 방 이름. 앞뒤 공백은 잘라낸 뒤 20자까지만 허용하고,
    빈 문자열이면 이름을 지움(로비 제목이 다시 기본 "로비"로 돌아감)."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]


class GoogleAuthIn(BaseModel):
    """id_token comes from Google Identity Services in the browser.
    current_code/current_token are this browser's existing room (if it
    has one) — sent along so that, the first time this Google account is
    used, we know which room to link it to instead of creating a new one."""

    id_token: str
    current_code: Optional[str] = None
    current_token: Optional[str] = None


class GoogleAuthOut(BaseModel):
    code: str
    token: str
    # True the first time a Google account gets linked to a room (so the
    # frontend can tell "just connected" apart from "found your room").
    linked_new: bool


class GoogleStatusOut(BaseModel):
    """GET /api/rooms/{code}/google — lets the frontend swap the sign-in
    button for "linked as <email>" once a room already has one linked."""

    linked: bool
    email: Optional[str] = None


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

    cat: Literal["book", "anime", "movie", "music"]
    title: str
    creator: Optional[str] = None
    rating: float = 0
    memo: Optional[str] = None
    photo_url: Optional[str] = None


class RecordOut(RecordIn):
    id: int
    date: str
    featured: bool = False


class RecordFeatureIn(BaseModel):
    """PUT .../records/{id}/feature 전용 바디 — 별점/메모 등 나머지
    필드는 안 건드리고 이 플래그 하나만 켜고/끄는 데 씀."""

    featured: bool


class UploadOut(BaseModel):
    url: str
