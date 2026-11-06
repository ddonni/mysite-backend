import re
from datetime import datetime
from typing import Annotated, Dict, List, Literal, Optional

from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, StringConstraints, field_validator


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


class RoomVisibilityIn(BaseModel):
    """PUT .../visibility — "방 둘러보기" 목록에 올릴지(true) 내릴지(false)."""

    public: bool


class PublicRoomOut(BaseModel):
    """GET /api/rooms 목록의 방 하나 — 누구나 볼 수 있는 값만(토큰·구글
    계정 정보는 절대 안 넣음). counts는 카테고리별 기록 수(없으면 0)."""

    code: str
    name: Optional[str] = None
    theme: str
    counts: Dict[str, int]
    total: int


class SuggestionOut(BaseModel):
    """GET .../suggestions 후보 하나 — value는 제목이나 만든 사람 이름(요청한 칸).
    mine은 이 방에 이미 있는 값인지, rooms는 몇 개 방에서 쓰였는지."""

    value: str
    mine: bool
    rooms: int


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
    # Only set when recovering a different room than the one this browser
    # sent as current_code/current_token (proven by its token): whether that
    # current room is still empty, so the frontend can switch without asking
    # (nothing to lose) or warn before the switch (its records would be left
    # behind). None in every other case.
    current_empty: Optional[bool] = None


class BookResult(BaseModel):
    """GET /api/search/book — one Kakao book search hit, trimmed to what the
    "새 기록" form fills in. title is cleaned for the form (edition notes and
    subtitle dropped); full_title is shown in the dropdown so similar
    editions can be told apart."""

    title: str
    full_title: str
    creator: str
    publisher: str
    year: str
    cover: Optional[str] = None


class MusicResult(BaseModel):
    """GET /api/search/music — one YouTube Music album (or, when signed-in
    search works, song) hit. The form fills in only creator and cover; the
    title the user typed stays. song is None for album results."""

    album: str
    song: Optional[str] = None
    creator: str
    kind: str
    year: str
    cover: Optional[str] = None
    # 틀 수 있는 유튜브 주소 — 노래 목록(by=songs)엔 바로 들어 있고, 앨범 결과엔 없는 대신
    # album_id(유튜브 뮤직 앨범 id)가 있어 고른 것만 /api/search/music/listen으로 물어봄.
    listen: Optional[str] = None
    album_id: Optional[str] = None


class MusicListenOut(BaseModel):
    listen: Optional[str] = None


class ScreenResult(BaseModel):
    """GET /api/search/screen — one TMDB movie/TV hit. No director here (it
    costs a request per title); the form asks GET /api/search/screen/creator
    for the one the user picks. original_title is "" when it's the same as
    title. creator is filled only for by=director searches (it's the
    director that was searched for), "" otherwise."""

    id: int
    media: Literal["movie", "tv"]
    title: str
    original_title: str
    year: str
    cover: Optional[str] = None
    creator: str = ""


class ScreenCreatorOut(BaseModel):
    creator: str


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


LISTEN_URL_MAX = 300
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def is_youtube_url(url: str) -> bool:
    """https 유튜브 주소이고 영상(v=, youtu.be/…) 또는 재생목록(list=) 번호가 있는지."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https":
        return False
    qs = parse_qs(u.query)
    ids = []
    if u.hostname == "youtu.be":
        ids.append(u.path.lstrip("/"))
    elif u.hostname in _YOUTUBE_HOSTS:
        ids += qs.get("v", []) + qs.get("list", [])
        if u.path.startswith("/shorts/"):
            ids.append(u.path[len("/shorts/"):])
    return any(_YOUTUBE_ID.match(i or "") for i in ids)


class RecordIn(BaseModel):
    """One 'watched/read it' log entry. photo_url comes from POST
    /api/uploads — the client uploads the photo first, then sends the
    returned URL along with the rest of the fields — or is a Kakao cover
    URL picked from GET /api/search/book (stored as-is, shown through
    GET /api/cover)."""

    cat: Literal["book", "anime", "movie", "music"]
    title: str
    creator: Optional[str] = None
    rating: float = 0
    memo: Optional[str] = None
    photo_url: Optional[str] = None
    listen_url: Optional[str] = None

    @field_validator("listen_url")
    @classmethod
    def _youtube_only(cls, v: Optional[str]) -> Optional[str]:
        """유튜브 주소만 — 방을 구경하는 사람도 누르는 링크라 아무 주소나 받지 않음."""
        v = (v or "").strip()
        if not v:
            return None
        if len(v) > LISTEN_URL_MAX or not is_youtube_url(v):
            raise ValueError("listen_url must be a YouTube link")
        return v


class RecordOut(RecordIn):
    id: int
    date: str
    featured: bool = False
    playing: bool = False


class RecordFeatureIn(BaseModel):
    """PUT .../records/{id}/feature 전용 바디 — 별점/메모 등 나머지
    필드는 안 건드리고 이 플래그 하나만 켜고/끄는 데 씀."""

    featured: bool


class RecordPlayingIn(BaseModel):
    """PUT .../records/{id}/playing 전용 바디 — 이 곡을 로비 턴테이블에
    올리거나(true) 내림(false)."""

    playing: bool


class UploadOut(BaseModel):
    url: str


class FeedbackIn(BaseModel):
    """POST /api/feedback — 앞뒤 공백을 자른 뒤 내용은 1~1000자, 이름은
    30자까지(빈 문자열이면 이름 없음). page는 보낸 화면의 경로(예:
    "/mysite/library"), room_code는 그때 보던 방 — 둘 다 맥락용이라 선택."""

    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    name: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]] = None
    page: Optional[Annotated[str, StringConstraints(max_length=200)]] = None
    room_code: Optional[Annotated[str, StringConstraints(max_length=10)]] = None


class FeedbackOut(BaseModel):
    """GET /api/admin/feedback의 한 건 — 운영자 피드백 페이지(admin.html)용."""

    id: int
    message: str
    name: Optional[str] = None
    page: Optional[str] = None
    room_code: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
