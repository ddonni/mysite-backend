"""새 기록 창의 외부 검색 API — 책(카카오), 음악(유튜브 뮤직), 영화·애니(TMDB), 그리고
카카오 표지 중계. main.py가 방·그림·기록 API만 담도록 여기로 뺌(main.py가 이 router를
include함). 실제 외부 호출과 결과 다듬기는 book_search.py / music_search.py /
screen_search.py가 하고, 여기는 횟수 제한·입력 검증·에러를 HTTP로 바꾸는 일만 함.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from . import book_search, music_search, schemas, screen_search
from .cache import TTLCache
from .ratelimit import check_rate_limit

router = APIRouter()

# 책 검색은 우리 서버가 카카오 키로 대신 묻는 것이라, 막지 않으면 남이 이
# 엔드포인트로 키의 하루 한도를 다 써버릴 수 있음. 제목 하나 칠 때 5~10번쯤
# 나가므로(입력 멈출 때마다) 한 학원 30명이 몇 권씩 기록해도 남게 600으로 둠.
_book_searches: Dict[str, List[datetime]] = {}
BOOK_SEARCH_LIMIT = 600
BOOK_SEARCH_WINDOW = timedelta(hours=1)

# 음악 검색은 키가 없는 대신, 우리 서버 IP 하나로 유튜브에 묻는 것이라 누가
# 이걸로 마구 검색하면 그 IP가 봇으로 찍혀 모두의 검색이 막힐 수 있음 — 책과
# 같은 한도를 따로 둠(책 검색을 많이 했다고 음악 검색까지 막히지 않게).
_music_searches: Dict[str, List[datetime]] = {}
MUSIC_SEARCH_LIMIT = 600
MUSIC_SEARCH_WINDOW = timedelta(hours=1)

# 영화·애니(TMDB)도 우리 토큰 하나로 묻는 것이라 같은 방식으로 따로 셈 — 감독
# 조회(고를 때 한 번)도 여기에 같이 셈.
_screen_searches: Dict[str, List[datetime]] = {}
SCREEN_SEARCH_LIMIT = 600
SCREEN_SEARCH_WINDOW = timedelta(hours=1)

# 표지는 한 번 받으면 바뀌지 않으니(주소에 timestamp가 들어 있음) 브라우저가
# 오래 캐시하게 함 — 같은 표지로 로비를 다시 열 때 우리 서버까지 안 옴.
COVER_CACHE_SECONDS = 30 * 24 * 3600

TOO_MANY = "too many searches, try again later"

# 같은 검색은 10분 동안 다시 묻지 않음(cache.py). 종류마다 따로 최대 500개 — 결과 한 건이
# 몇 KB라 다 차도 몇 MB. 감독 조회도 같은 작품을 여러 사람이 고르니 같이 기억함.
SEARCH_CACHE_SECONDS = 10 * 60
_book_cache = TTLCache(SEARCH_CACHE_SECONDS, 500)
_music_cache = TTLCache(SEARCH_CACHE_SECONDS, 500)
_screen_cache = TTLCache(SEARCH_CACHE_SECONDS, 500)
SEARCH_CACHES = (_book_cache, _music_cache, _screen_cache)


@router.get("/api/search/book", response_model=List[schemas.BookResult])
def search_book(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100),
    by: Literal["title", "author"] = "title",
):
    """"새 기록" 창의 제목·작가 검색 — 카카오 책 검색을 대신 불러 표지·작가까지
    채울 수 있게 다듬어 돌려줌. by=author면 작가 이름으로만 찾음(작가 칸).
    키(KAKAO_REST_KEY)가 없거나 카카오가 응답을 못 하면 503 — 프론트는 이때
    지금처럼 직접 입력만 하게 됨."""
    check_rate_limit(request, _book_searches, BOOK_SEARCH_LIMIT, BOOK_SEARCH_WINDOW, TOO_MANY)
    try:
        q = q.strip()
        return _book_cache.get_or_set((q, by), lambda: book_search.search_books(q, by_author=(by == "author")))
    except book_search.SearchUnavailable:
        raise HTTPException(status_code=503, detail="book search unavailable")


@router.get("/api/search/music", response_model=List[schemas.MusicResult])
def search_music(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100),
    by: Literal["title", "artist"] = "title",
):
    """"새 기록" 창 음악 탭의 앨범 검색 — 유튜브 뮤직(비공식 ytmusicapi)에서
    앨범(가끔 노래)을 찾아 가수·커버를 채울 수 있게 돌려줌(music_search.py
    위쪽 설명). by=artist(가수 칸)면 그 가수의 앨범·싱글 목록(일반 검색은 앨범
    3개뿐이라). 유튜브가 응답 형식을 바꾸거나 막으면 503 — 프론트는 이때 직접
    입력만 하게 됨."""
    check_rate_limit(request, _music_searches, MUSIC_SEARCH_LIMIT, MUSIC_SEARCH_WINDOW, TOO_MANY)
    try:
        q = q.strip()
        if by == "artist":
            return _music_cache.get_or_set((q, by), lambda: music_search.search_artist(q))
        return _music_cache.get_or_set((q, by), lambda: music_search.search_music(q))
    except music_search.SearchUnavailable:
        raise HTTPException(status_code=503, detail="music search unavailable")


@router.get("/api/search/screen", response_model=List[schemas.ScreenResult])
def search_screen(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100),
    kind: Literal["movie", "anime"] = "movie",
    by: Literal["title", "director"] = "title",
):
    """"새 기록" 창 영화·애니 탭의 검색 — TMDB에서 한국어 제목·포스터를 찾아 줌
    (screen_search.py 위쪽 설명). by=title이면 제목으로(감독은 고른 작품만 따로
    /api/search/screen/creator), by=director면 그 감독의 연출작(감독 이름이
    creator에 들어 있음). 토큰(TMDB_TOKEN)이 없거나 TMDB가 응답을 못 하면 503."""
    check_rate_limit(request, _screen_searches, SCREEN_SEARCH_LIMIT, SCREEN_SEARCH_WINDOW, TOO_MANY)
    try:
        q = q.strip()
        if by == "director":
            return _screen_cache.get_or_set((q, kind, by), lambda: screen_search.search_by_director(q, kind))
        return _screen_cache.get_or_set((q, kind, by), lambda: screen_search.search_screen(q, kind))
    except screen_search.SearchUnavailable:
        raise HTTPException(status_code=503, detail="screen search unavailable")


@router.get("/api/search/screen/creator", response_model=schemas.ScreenCreatorOut)
def screen_creator(request: Request, media: Literal["movie", "tv"], id: int = Query(..., ge=1)):
    """검색 결과에서 고른 영화·TV의 감독 — 못 찾으면 빈 문자열(그 칸은 사용자가
    직접 씀)."""
    check_rate_limit(request, _screen_searches, SCREEN_SEARCH_LIMIT, SCREEN_SEARCH_WINDOW, TOO_MANY)
    try:
        return {"creator": _screen_cache.get_or_set(("creator", media, id), lambda: screen_search.find_creator(media, id))}
    except screen_search.SearchUnavailable:
        raise HTTPException(status_code=503, detail="screen search unavailable")


@router.get("/api/cover")
def relay_cover(url: str = Query(..., max_length=1000)):
    """카카오 표지를 우리 CORS 헤더로 다시 내보냄(book_search.py 위쪽 설명).
    카카오 표지 서버만 허용 — 그 밖의 주소는 400."""
    try:
        body, content_type = book_search.fetch_cover(url)
    except book_search.CoverNotAllowed:
        raise HTTPException(status_code=400, detail="cover host not allowed")
    except book_search.CoverFetchFailed:
        raise HTTPException(status_code=502, detail="cover fetch failed")
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": f"public, max-age={COVER_CACHE_SECONDS}, immutable"},
    )
