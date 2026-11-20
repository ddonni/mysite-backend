import re
from datetime import datetime
from typing import Annotated, Dict, List, Literal, Optional

from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from . import storage

# storage.upload_photo가 만드는 키 이름(uuid hex + 확장자).
_UPLOADED_KEY = re.compile(r"^[0-9a-f]{32}\.[a-z]{3,4}$")


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


class RoomBioIn(BaseModel):
    """PUT .../bio — 기록을 소개하는 한 줄. 앞뒤 공백을 잘라 40자까지, 빈 문자열이면 지움."""

    bio: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]


class RoomAvatarIn(BaseModel):
    """PUT .../avatar — 프로필 사진. POST .../uploads가 돌려준 우리 S3 주소만 받음(남의 서버
    주소를 넣어 방문자 브라우저가 아무 데나 요청하게 만들지 못하게). 비우거나 null이면 기본 사진."""

    avatar_url: Optional[str] = None

    @field_validator("avatar_url")
    @classmethod
    def _uploaded_only(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if not v:
            return None
        prefix = storage.uploaded_url_prefix()
        if len(v) > 300 or not v.startswith(prefix) or not _UPLOADED_KEY.match(v[len(prefix):]):
            raise ValueError("avatar_url must be an uploaded photo")
        return v


class RoomVisibilityIn(BaseModel):
    """PUT .../visibility — "방 둘러보기" 목록에 올릴지(true) 내릴지(false)."""

    public: bool


class FeaturedWork(BaseModel):
    id: int
    cat: str
    title: str
    photo_url: Optional[str] = None


class PublicRoomOut(BaseModel):
    """GET /api/rooms 목록의 방 하나 — 누구나 볼 수 있는 값만(토큰·구글
    계정 정보는 절대 안 넣음). counts는 카테고리별 기록 수(없으면 0)."""

    code: str
    name: Optional[str] = None
    theme: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    likes: int = 0
    # 인생작(카테고리마다 최대 3개 × 4) — 카드에 작은 표지로.
    featured: List["FeaturedWork"] = []
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


# 세부 기록에서 카테고리마다 받는 요소별 별점과 장르 — 프론트 js/library/modal/detailFields.js와 같아야 함.
DETAIL_ASPECTS = {
    "anime": ("novelty", "story", "art", "character", "ending"),
    "movie": ("story", "acting", "visual", "music", "ending"),
    "book": ("ending",),
}
DETAIL_GENRES = {
    "anime": ("액션", "판타지", "이세계", "로맨스", "일상", "코미디", "SF", "미스터리", "스포츠", "드라마", "공포", "학원"),
    "movie": ("액션", "드라마", "로맨스", "코미디", "스릴러", "범죄", "SF", "판타지", "공포", "미스터리", "애니메이션", "다큐"),
    "book": ("소설", "에세이", "시", "인문", "역사", "과학", "자기계발", "경제", "만화", "판타지", "추리", "로맨스"),
}
GENRES_MAX = 5


class RecordDetails(BaseModel):
    """세부 기록 — scores는 요소 → 별점(0.5~5, 0.5 단위), genres는 고른 장르(최대 GENRES_MAX개).
    카테고리에 맞는지는 RecordIn이 거름(모르는 요소·장르는 422)."""

    scores: Dict[str, float] = {}
    genres: List[str] = []

    @field_validator("scores")
    @classmethod
    def _half_stars(cls, v: Dict[str, float]) -> Dict[str, float]:
        for key, score in v.items():
            if not (0.5 <= score <= 5) or (score * 2) != int(score * 2):
                raise ValueError(f"score for {key} must be 0.5..5 in 0.5 steps")
        return v


# 카테고리 — 작품 넷 + 옷·일기(2026-11-20). 옷·일기는 **방 주인만** 봄(GET 목록은 토큰이 맞을 때만
# 넣고, 둘러보기 수·인생작·자동 완성·공감에서도 빠짐 — crud.list_records, list_public_rooms).
WORK_CATS = ("book", "anime", "movie", "music")
PRIVATE_CATS = ("clothes", "diary")
CATS = WORK_CATS + PRIVATE_CATS

# 옷 칸에서 고르는 값 — 프론트 js/library/modal/clothesFields.js와 같아야 함. 나중에 코디·OOTD에서
# 묶어 쓰기 좋게 정해진 값만 받음(태그·소재·사이즈는 자유롭게).
CLOTHES_KINDS = ("상의", "하의", "아우터", "원피스", "신발", "가방", "모자", "액세서리")
CLOTHES_COLORS = (
    "블랙", "화이트", "그레이", "베이지", "브라운", "네이비", "블루", "스카이블루",
    "그린", "카키", "레드", "핑크", "퍼플", "옐로", "오렌지", "멀티",
)
CLOTHES_SEASONS = ("봄", "여름", "가을", "겨울")
CLOTHES_FITS = ("슬림", "레귤러", "세미오버", "오버")
CLOTHES_TONES = ("웜톤", "쿨톤", "뉴트럴")
CLOTHES_TAGS_MAX = 10
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(v: Optional[str]) -> Optional[str]:
    """빈 값은 None, 아니면 실제로 있는 날짜의 YYYY-MM-DD만."""
    v = (v or "").strip()
    if not v:
        return None
    if not _DATE.match(v):
        raise ValueError("date must be YYYY-MM-DD")
    datetime.strptime(v, "%Y-%m-%d")  # 2월 30일 같은 날짜는 ValueError
    return v


class ClothesAttrs(BaseModel):
    """옷 하나의 추가 칸(전부 선택). 계절은 여러 개(같은 값은 한 번, 봄→겨울 순으로),
    태그는 앞뒤 공백을 잘라 15자까지 최대 10개."""

    purchased: Optional[str] = None
    kind: Optional[Literal[CLOTHES_KINDS]] = None
    color: Optional[Literal[CLOTHES_COLORS]] = None
    seasons: List[Literal[CLOTHES_SEASONS]] = []
    fit: Optional[Literal[CLOTHES_FITS]] = None
    tone: Optional[Literal[CLOTHES_TONES]] = None
    tags: List[Annotated[str, StringConstraints(strip_whitespace=True, max_length=15)]] = []
    material: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]] = None
    size: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=10)]] = None

    @field_validator("purchased")
    @classmethod
    def _purchased(cls, v: Optional[str]) -> Optional[str]:
        return _valid_date(v)

    @field_validator("seasons")
    @classmethod
    def _seasons(cls, v: list) -> list:
        return [s for s in CLOTHES_SEASONS if s in v]

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list) -> list:
        tags = list(dict.fromkeys(t for t in v if t))
        if len(tags) > CLOTHES_TAGS_MAX:
            raise ValueError(f"at most {CLOTHES_TAGS_MAX} tags")
        return tags

    @field_validator("material", "size")
    @classmethod
    def _blank_is_none(cls, v: Optional[str]) -> Optional[str]:
        return v or None


DIARY_MAX = 10000
DIARY_PHOTOS_MAX = 20  # 프론트 js/diary/photos.js의 PHOTOS_MAX와 같아야 함


class RecordIn(BaseModel):
    """One 'watched/read it' log entry. photo_url comes from POST
    /api/uploads — the client uploads the photo first, then sends the
    returned URL along with the rest of the fields — or is a Kakao cover
    URL picked from GET /api/search/book (stored as-is, shown through
    GET /api/cover)."""

    cat: Literal[CATS]
    title: str
    creator: Optional[str] = None
    rating: float = 0
    memo: Optional[str] = None
    photo_url: Optional[str] = None
    listen_url: Optional[str] = None
    details: Optional[RecordDetails] = None
    # 보는 중·읽는 중 — 음악엔 없어서 음악이면 늘 False로 저장.
    in_progress: bool = False
    # 옷의 추가 칸(옷이 아니면 버림).
    attrs: Optional[ClothesAttrs] = None
    # 일기의 날짜(YYYY-MM-DD) — 어제 일을 오늘 쓰는 일이 많아서 일기만 고를 수 있음. 안 주면(또는 다른
    # 카테고리면) 저장할 때 오늘(새 기록) / 그대로(수정).
    date: Optional[str] = None
    # 일기의 사진들(2026-11-20, 여러 장 — POST .../uploads가 돌려준 주소들, 보여 줄 순서대로). 첫 장이 카드
    # 표지라 photo_url은 늘 photos[0]으로 맞춤. 일기가 아니면 버림.
    photos: Optional[List[str]] = None

    @model_validator(mode="after")
    def _private_categories(self) -> "RecordIn":
        """옷: 이름(title)·사진이 꼭 있어야 함. 일기: 본문(memo)이 꼭 있어야 하고 제목은 비워도 됨.
        둘 다 별점·보는 중·세부 기록·유튜브 링크가 없음. 요청에만 적용 — RecordOut(응답)은 이 클래스를
        물려받지만 date가 저장된 날짜라서 여기서 지우면 안 됨."""
        if type(self) is not RecordIn:
            return self
        if self.cat != "clothes":
            self.attrs = None
        elif self.attrs is None:
            self.attrs = ClothesAttrs()
        self.date = _valid_date(self.date) if self.cat == "diary" else None
        if self.cat == "diary":
            # 예전처럼 photo_url 하나만 보내도 한 장짜리 목록으로. 같은 주소는 한 번만.
            photos = self.photos if self.photos is not None else ([self.photo_url] if self.photo_url else [])
            photos = list(dict.fromkeys(p.strip() for p in photos if p and p.strip()))
            if len(photos) > DIARY_PHOTOS_MAX:
                raise ValueError(f"at most {DIARY_PHOTOS_MAX} photos")
            if any(len(p) > 500 or not p.startswith(("https://", "http://")) for p in photos):
                raise ValueError("photos must be uploaded photo URLs")
            self.photos = photos
            self.photo_url = photos[0] if photos else None
        else:
            self.photos = None
        if self.cat not in PRIVATE_CATS:
            return self
        self.title = self.title.strip()
        self.rating = 0
        self.in_progress = False
        self.details = None
        self.listen_url = None
        if self.cat == "clothes" and (not self.title or not self.photo_url):
            raise ValueError("clothes need a name and a photo")
        if self.cat == "diary":
            self.memo = (self.memo or "").strip()
            if not self.memo:
                raise ValueError("diary needs some text")
            if len(self.memo) > DIARY_MAX:
                raise ValueError(f"diary is at most {DIARY_MAX} characters")
        return self

    @model_validator(mode="after")
    def _details_for_category(self) -> "RecordIn":
        if self.cat == "music":
            self.in_progress = False
        # 음악(또는 비운 세부 기록)은 저장하지 않음. 그 밖엔 이 카테고리의 요소·장르만 받음.
        d = self.details
        if d is None or self.cat not in DETAIL_ASPECTS or (not d.scores and not d.genres):
            self.details = None
            return self
        bad = [k for k in d.scores if k not in DETAIL_ASPECTS[self.cat]]
        bad += [g for g in d.genres if g not in DETAIL_GENRES[self.cat]]
        if bad:
            raise ValueError(f"unknown detail for {self.cat}: {bad}")
        genres = list(dict.fromkeys(d.genres))
        if len(genres) > GENRES_MAX:
            raise ValueError(f"at most {GENRES_MAX} genres")
        self.details = RecordDetails(scores=d.scores, genres=genres)
        return self

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
    """응답은 저장된 값 그대로 — 위 검사(옷은 사진 필수 등)를 다시 하지 않음."""

    id: int
    date: str
    featured: bool = False
    playing: bool = False
    # 티어리스트에 직접 놓은 줄과 순서(없으면 null — 별점으로 자동 배치).
    tier: Optional[str] = None
    tier_pos: Optional[int] = None
    # 카드 공감 — likes는 받은 수, liked는 요청한 사람(X-Actor-*)이 눌렀는지. 목록 GET에서만 채움.
    likes: int = 0
    liked: bool = False


TIERS = ("SS", "S", "A", "B", "C", "D", "F", "-")


class TierPlacement(BaseModel):
    """티어리스트에 놓은 기록 하나. tier가 null이면 "별점으로 자동"으로 되돌림(pos도 지움).
    "-"는 미배치 칸에 직접 놓은 것."""

    id: int
    tier: Optional[Literal[TIERS]] = None
    pos: Optional[int] = Field(default=None, ge=0, le=100000)


class TierListIn(BaseModel):
    """PUT .../tierlist — 옮길 때마다 바뀐 줄들의 기록을 한꺼번에 보냄(최대 2000개)."""

    items: List[TierPlacement] = Field(max_length=2000)


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


class GuestbookIn(BaseModel):
    """POST .../guestbook — 앞뒤 공백을 자른 뒤 1~300자. anonymous면 남에게 이름·사진을 숨김."""

    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    anonymous: bool = False


class GuestbookAuthor(BaseModel):
    code: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class GuestbookOut(BaseModel):
    """방명록 글 하나. author는 익명 글이면(쓴 본인이 아닌 한) null. mine = 내가 쓴 글,
    can_delete = 내가 지울 수 있음(글쓴이 본인 또는 방 주인)."""

    id: int
    message: str
    anonymous: bool
    created_at: Optional[datetime] = None
    author: Optional[GuestbookAuthor] = None
    mine: bool = False
    by_owner: bool = False
    can_delete: bool = False


class GuestbookListOut(BaseModel):
    total: int
    entries: List[GuestbookOut]


class LikesOut(BaseModel):
    count: int
    liked: bool


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
