"""Album lookup for the "새 기록" form's music tab, through ytmusicapi
(YouTube Music's internal web API — unofficial, so it can break whenever
YouTube changes its responses; callers must treat failure as "no results").

What works without signing in (checked 2026-10-25, from home and from the
EC2 server): album/single results with Korean titles and artist names
("꽃갈피 / 아이유(IU)") and 544px covers on yt3.googleusercontent.com, which
sends Access-Control-Allow-Origin: * — so unlike Kakao book covers these need
no relay. What doesn't: song results. YouTube answers an anonymous song
search with "검색결과가 없습니다", so a bare song title often finds only
covers by other artists; adding the artist ("아이유 좋은 날") finds the
original album. The frontend therefore searches "artist + title" when the
artist field is filled, and fills in only the artist and cover on a pick —
the title the user typed (usually a song) stays.

YTMUSIC_AUTH_FILE (optional) points at a ytmusicapi browser-auth JSON for a
throwaway Google account, in case signed-in searches turn out to return
songs. If that search fails (expired cookies, blocked account) it falls back
to the anonymous search, so the site keeps working either way.

Kept in its own module, like book_search.py, so tests can monkeypatch
_search instead of calling YouTube.
"""
import os
from typing import List, Optional

import requests
from ytmusicapi import YTMusic

TIMEOUT = 5
MAX_RESULTS = 8


class SearchUnavailable(Exception):
    pass


class _TimeoutSession(requests.Session):
    # ytmusicapi는 요청에 timeout을 안 줌 — 유튜브가 늦게 답하면 검색 하나가
    # 서버 스레드를 붙잡고 끝없이 기다리게 되니 기본값을 붙임.
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return super().request(*args, **kwargs)


_clients = {}


def _client(auth_file: Optional[str]) -> YTMusic:
    key = auth_file or ""
    if key not in _clients:
        _clients[key] = YTMusic(auth=auth_file or None, language="ko", requests_session=_TimeoutSession())
    return _clients[key]


def _search(query: str, auth_file: Optional[str]) -> list:
    return _client(auth_file).search(query)


def _artist(channel_id: str) -> dict:
    return _client(None).get_artist(channel_id)


def _cover(result: dict) -> Optional[str]:
    thumbs = result.get("thumbnails") or []
    return thumbs[-1].get("url") if thumbs else None


def _to_item(r: dict) -> Optional[dict]:
    artists = ", ".join(a.get("name", "") for a in r.get("artists") or [] if a.get("name"))
    if r.get("resultType") == "album":
        return {
            "album": r.get("title") or "",
            "song": None,
            "creator": artists,
            "kind": r.get("type") or "",
            "year": str(r.get("year") or ""),
            "cover": _cover(r),
        }
    if r.get("resultType") == "song":
        return {
            "album": (r.get("album") or {}).get("name") or "",
            "song": r.get("title") or "",
            "creator": artists,
            "kind": "노래",
            "year": str(r.get("year") or ""),
            "cover": _cover(r),
        }
    return None


def search_music(query: str) -> List[dict]:
    auth_file = os.environ.get("YTMUSIC_AUTH_FILE", "").strip() or None
    results = None
    if auth_file:
        try:
            results = _search(query, auth_file)
        except Exception:
            results = None  # 로그인 검색이 막혀도 비로그인 검색으로 계속
    if results is None:
        try:
            results = _search(query, None)
        except Exception as e:
            raise SearchUnavailable(str(e))

    return _items(results)


def _items(results: list) -> List[dict]:
    out = []
    for r in results:
        item = _to_item(r)
        if item and (item["album"] or item["song"]):
            out.append(item)
    return out[:MAX_RESULTS]


ARTIST_MAX_RESULTS = 12


def search_artist(query: str) -> List[dict]:
    """가수 이름으로 찾기(가수 칸) — 일반 검색은 앨범을 3개까지만 줘서, 그 가수의
    페이지(get_artist)에서 앨범·싱글 목록을 가져옴(앨범 먼저). 가수는 검색 결과
    앨범에 붙은 가수 id로 찾음 — 맨 위 "가수" 결과 카드는 ytmusicapi가 이름·id를
    못 읽는 일이 있어서(2026-10-25 확인). 가수를 못 찾거나 가수 페이지가 실패하면
    일반 검색 결과(search_music)로 대신함."""
    try:
        results = _search(query, None)
    except Exception as e:
        raise SearchUnavailable(str(e))
    found = _items(results)
    channel = next(
        (a.get("id") for r in results if r.get("resultType") == "album"
         for a in (r.get("artists") or []) if a.get("id")),
        None,
    )
    if not channel:
        return found
    try:
        page = _artist(channel)
    except Exception:
        return found
    name = page.get("name") or ""
    out = []
    for section, kind in (("albums", "앨범"), ("singles", "싱글")):
        for r in (page.get(section) or {}).get("results") or []:
            if not r.get("title"):
                continue
            out.append({
                "album": r["title"],
                "song": None,
                "creator": name,
                "kind": kind,
                "year": str(r.get("year") or ""),
                "cover": _cover(r),
            })
    return out[:ARTIST_MAX_RESULTS] or found
