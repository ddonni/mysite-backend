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

Song titles: because anonymous YouTube Music can't search songs, a title
search also asks the iTunes Search API (free, no key) which album the song
is on — "나랑 아니면" → The Black Skirts / TEAM BABY. The Korean store has no
song catalog, so this uses the US store, which still matches Korean titles
but answers with English names. So iTunes only identifies the album; the
album itself (Korean title and artist, cover) is then looked up on YouTube
Music ("The Black Skirts TEAM BABY" → 검정치마 / TEAM BABY), falling back to
the iTunes names and artwork (is1-ssl.mzstatic.com, CORS-open) when YouTube
has no matching album. Apple allows roughly 20 calls a minute per IP and
this is one server IP for everyone, so iTunes calls are capped below that
(_itunes_allowed) — over the cap, or when iTunes fails, search just skips
it and returns the YouTube results as before.

Kept in its own module, like book_search.py, so tests can monkeypatch
_search / _itunes instead of calling YouTube / Apple.
"""
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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


def _album(browse_id: str) -> dict:
    return _client(None).get_album(browse_id)


ITUNES_URL = "https://itunes.apple.com/search"
# 애플이 밝힌 한도(IP당 분당 약 20번)보다 조금 아래로 — 넘기면 한동안 403으로 막힘.
ITUNES_PER_MINUTE = 18
_itunes_calls: deque = deque()
_itunes_lock = threading.Lock()


def _itunes_allowed() -> bool:
    """이번 1분 안에 iTunes를 더 불러도 되는지 — 되면 이번 호출을 셈에 넣음."""
    now = time.monotonic()
    with _itunes_lock:
        while _itunes_calls and now - _itunes_calls[0] > 60:
            _itunes_calls.popleft()
        if len(_itunes_calls) >= ITUNES_PER_MINUTE:
            return False
        _itunes_calls.append(now)
        return True


def _itunes(query: str) -> list:
    resp = requests.get(
        ITUNES_URL,
        params={"term": query, "country": "US", "entity": "song", "limit": 3},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("results") or []


def _squash(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "").lower()


def _same(a: str, b: str) -> bool:
    """앨범 이름 비교 — 띄어쓰기·기호·대소문자를 빼고 한쪽이 다른 쪽을 품으면 같은 이름으로
    봄("LEGEND(전설)" ↔ "Legend"). 짧은 쪽이 긴 쪽의 절반은 돼야 함 — "Origin"이 "Sex
    Education Original Soundtrack"에 걸리지 않게."""
    a, b = _squash(a), _squash(b)
    short, long_ = sorted((a, b), key=len)
    return bool(short) and short in long_ and len(short) * 2 >= len(long_)


# 가수 이름의 조각 — "아이유(IU)" → 아이유, IU / "Agust D & 아이유" → Agust D, 아이유.
_ARTIST_SPLIT = re.compile(r"[()（）\[\],&/]|\s+(?:feat\.?|x|with)\s+", re.IGNORECASE)


def _artist_keys(names: List[str]) -> set:
    keys = set()
    for name in names:
        keys.add(_squash(name))
        keys.update(_squash(p) for p in _ARTIST_SPLIT.split(name))
    keys.discard("")
    return keys


def _same_artist(yt_artists: list, itunes_artist: str) -> bool:
    """가수 비교 — 유튜브는 "아이유(IU)", "래원 (Layone)"처럼 한국어·영어를 같이 쓰는 일이
    많아서, 조각 중 하나가 iTunes 가수 이름(의 조각)과 똑같으면 같은 가수로 봄. 앨범과 달리
    품기만 해서는 안 됨 — "IU"가 아무 이름에나 들어 있을 수 있어서."""
    names = [a.get("name", "") for a in yt_artists or []]
    return bool(_artist_keys(names) & _artist_keys([itunes_artist]))


# iTunes 앨범 이름 끝의 " - Single" / " - EP" — 떼고, 종류(kind)로 씀.
_ITUNES_KIND = re.compile(r"\s+-\s+(Single|EP)$")


def _song_album(query: str) -> Optional[dict]:
    """노래 제목(가수를 붙였을 수도 있음)이 실린 앨범 하나 — 못 찾으면 None."""
    if not _itunes_allowed():
        return None
    try:
        songs = _itunes(query)
    except Exception:
        return None  # iTunes가 막히거나 늦으면 유튜브 결과만으로
    song = next((s for s in songs if s.get("collectionName") and s.get("artistName")), None)
    if not song:
        return None
    m = _ITUNES_KIND.search(song["collectionName"])
    album = _ITUNES_KIND.sub("", song["collectionName"])
    artist = song["artistName"]
    year = (song.get("releaseDate") or "")[:4]
    # 유튜브 뮤직에서 같은 앨범의 한국어 이름·가수·커버. 이름이 한쪽만 번역된 경우가 흔해서
    # (가수 "The Black Skirts" ↔ "검정치마", 앨범 "Origin" ↔ "원래", "Through the Night" ↔
    # "밤편지") 앨범·가수 둘 다 맞는 결과 → 가수가 맞는 첫 앨범 → 앨범 이름만 맞는 결과 순.
    # 가수를 앨범 이름보다 먼저 봄 — "Through the Night"처럼 흔한 이름은 다른 가수의 같은
    # 이름 앨범이 걸리지만, 검색어에 가수와 앨범을 같이 넣어서 그 가수의 첫 앨범은 대개 맞음.
    try:
        albums = [r for r in _search(f"{artist} {album}", None) if r.get("resultType") == "album"]
    except Exception:
        albums = []
    by_artist = lambda r: _same_artist(r.get("artists"), artist)
    hit = (next((r for r in albums if _same(r.get("title"), album) and by_artist(r)), None)
           or next((r for r in albums if by_artist(r)), None)
           or next((r for r in albums if _same(r.get("title"), album)), None))
    if hit:
        item = _to_item(hit)
        item["year"] = item["year"] or year
        return item
    art = song.get("artworkUrl100") or ""
    return {
        "album": album,
        "song": None,
        "creator": artist,
        "kind": {"Single": "싱글", "EP": "EP"}.get(m.group(1) if m else "", "앨범"),
        "year": year,
        "cover": art.replace("100x100bb", "600x600bb") or None,
    }


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

    items = _items(results)
    # 노래가 실린 앨범을 맨 앞에(유튜브 결과에 같은 앨범이 있으면 그건 뺌). 제목은
    # 사용자가 쓴 그대로 두는 게 규칙이라 song은 비워 둠 — iTunes 노래 제목은 영어임.
    found = _song_album(query)
    if found:
        items = [found] + [i for i in items if not (i["album"] == found["album"] and i["creator"] == found["creator"])]
    return items[:MAX_RESULTS]


def _items(results: list) -> List[dict]:
    out = []
    for r in results:
        item = _to_item(r)
        if item and (item["album"] or item["song"]):
            out.append(item)
    return out[:MAX_RESULTS]


ARTIST_MAX_RESULTS = 12


def _artist_page(query: str):
    """가수 이름 → (일반 검색 결과, 가수 페이지 또는 None). 가수는 검색 결과 앨범에 붙은
    가수 id로 찾음 — 맨 위 "가수" 결과 카드는 ytmusicapi가 이름·id를 못 읽는 일이 있어서
    (2026-10-25 확인). 검색 자체가 실패하면 SearchUnavailable."""
    try:
        results = _search(query, None)
    except Exception as e:
        raise SearchUnavailable(str(e))
    channel = next(
        (a.get("id") for r in results if r.get("resultType") == "album"
         for a in (r.get("artists") or []) if a.get("id")),
        None,
    )
    if not channel:
        return results, None
    try:
        return results, _artist(channel)
    except Exception:
        return results, None


def _releases(page: dict):
    """가수 페이지의 앨범·싱글 — (종류, 결과) 순서대로, 앨범 먼저."""
    for section, kind in (("albums", "앨범"), ("singles", "싱글")):
        for r in (page.get(section) or {}).get("results") or []:
            if r.get("title"):
                yield kind, r


def search_artist(query: str) -> List[dict]:
    """가수 이름으로 찾기(가수 칸) — 일반 검색은 앨범을 3개까지만 줘서, 그 가수의
    페이지(get_artist)에서 앨범·싱글 목록을 가져옴(앨범 먼저). 가수를 못 찾거나 가수
    페이지가 실패하면 일반 검색 결과(search_music)로 대신함."""
    results, page = _artist_page(query)
    found = _items(results)
    if not page:
        return found
    name = page.get("name") or ""
    out = []
    for kind, r in _releases(page):
        out.append({
            "album": r["title"],
            "song": None,
            "creator": name,
            "kind": kind,
            "year": str(r.get("year") or ""),
            "cover": _cover(r),
        })
    return out[:ARTIST_MAX_RESULTS] or found


# 가수의 노래 목록에서 수록곡을 받아 올 앨범·싱글 수와 돌려줄 노래 수. 앨범 하나에 유튜브
# 요청이 하나씩 더 나가서(get_album) 묶음 수를 정해 둠 — 결과는 검색 캐시(10분)에 남음.
SONGS_RELEASES = 12
SONGS_MAX_RESULTS = 150
SONGS_WORKERS = 4


def search_artist_songs(query: str) -> List[dict]:
    """가수 이름으로 그 가수의 노래 목록 — "여러 개 추가"의 검색해서 고르기에서 들은 노래만
    체크하게. 앨범 이름은 모르고 노래만 아는 경우가 많아서 앨범이 아니라 노래로 돌려줌
    (song에 노래 제목, album은 어느 앨범인지 구분용). 로그인 없이는 가수 페이지의 인기곡
    목록이 비어 와서(2026-10-29 확인), 앨범·싱글마다 수록곡(get_album)을 받아 모음 — 앨범
    먼저, 같은 노래(싱글로 먼저 나온 곡 등)는 처음 것만. 가수를 못 찾으면 빈 목록,
    앨범 하나가 실패하면 그 앨범만 뺌."""
    _, page = _artist_page(query)
    if not page:
        return []
    name = page.get("name") or ""
    releases = [(kind, r) for kind, r in _releases(page) if r.get("browseId")][:SONGS_RELEASES]

    def tracks(item):
        try:
            return (_album(item[1]["browseId"]).get("tracks") or [])
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=SONGS_WORKERS) as pool:
        tracklists = list(pool.map(tracks, releases))

    out, seen = [], set()
    for (kind, r), tracks_of in zip(releases, tracklists):
        for t in tracks_of:
            key = _squash(t.get("title"))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({
                "album": r["title"],
                "song": t["title"],
                "creator": name,
                "kind": "노래",
                "year": str(r.get("year") or ""),
                "cover": _cover(r),
            })
    return out[:SONGS_MAX_RESULTS]
