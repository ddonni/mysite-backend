"""Movie/anime lookup for the "새 기록" form, through TMDB (The Movie
Database). Free for non-commercial use with attribution ("정보 제공: TMDB"
under the results); about 40 requests/second per IP, no daily cap.

Checked 2026-10-25: searching in Korean with language=ko-KR finds Korean
titles for both films ("인터스텔라", "파리, 텍사스") and anime ("신세계에서",
"장송의 프리렌"), and directors come back in Korean too ("봉준호",
"미야자키 하야오"). Posters on image.tmdb.org send Access-Control-Allow-Origin:
*, so unlike Kakao book covers they need no relay.

Search results carry no director, and getting one costs a request per title,
so search stays one request and the director is looked up separately
(find_creator) only for the result the user picks.

TMDB_TOKEN is the "API 읽기 액세스 토큰" (v4 bearer token), sent as a header
so the key never shows up in URLs or logs. Kept in its own module, like
book_search.py, so tests can monkeypatch _tmdb_get.
"""
import os
from typing import List, Optional

import requests

TMDB_API = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w342"
ANIMATION_GENRE = 16
MAX_RESULTS = 8
TIMEOUT = 5


class SearchUnavailable(Exception):
    """No TMDB_TOKEN configured, or TMDB didn't answer properly."""


def _tmdb_get(path: str, params: dict, token: str) -> dict:
    resp = requests.get(
        TMDB_API + path,
        params={"language": "ko-KR", **params},
        headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: Optional[dict] = None) -> dict:
    token = os.environ.get("TMDB_TOKEN", "").strip()
    if not token:
        raise SearchUnavailable("TMDB_TOKEN is not set")
    try:
        return _tmdb_get(path, params or {}, token)
    except (requests.RequestException, ValueError) as e:
        raise SearchUnavailable(str(e))


def _to_item(r: dict, media: str) -> dict:
    title = (r.get("title") or r.get("name") or "").strip()
    original = (r.get("original_title") or r.get("original_name") or "").strip()
    date = r.get("release_date") or r.get("first_air_date") or ""
    poster = r.get("poster_path")
    return {
        "id": r.get("id"),
        "media": media,
        "title": title,
        "original_title": original if original != title else "",
        "year": date[:4],
        "cover": POSTER_BASE + poster if poster else None,
    }


def search_screen(query: str, kind: str) -> List[dict]:
    """kind="movie": 영화 전체(애니 극장판도 영화로 기록하는 사람이 있어서 안 거름).
    kind="anime": 영화·TV를 한꺼번에 찾아 애니메이션 장르만 남김."""
    params = {"query": query, "include_adult": "false"}
    if kind == "movie":
        results = _get("/search/movie", params).get("results", [])
        items = [_to_item(r, "movie") for r in results]
    else:
        results = _get("/search/multi", params).get("results", [])
        items = [
            _to_item(r, r["media_type"])
            for r in results
            if r.get("media_type") in ("movie", "tv") and ANIMATION_GENRE in (r.get("genre_ids") or [])
        ]
    return [i for i in items if i["title"] and i["id"] is not None][:MAX_RESULTS]


# TV 애니의 aggregate_credits에는 회차 연출까지 "Director"로 잔뜩 들어 있어서,
# 시리즈 전체 감독을 뜻하는 직함을 먼저 찾음. 없으면 맨 앞 Director(TMDB가
# 참여 회차가 많은 순으로 줘서 대개 메인 감독 — 진격의 거인·프리렌·비밥으로 확인).
_SERIES_DIRECTOR_JOBS = ("Series Director", "Chief Director")


def find_creator(media: str, tmdb_id: int) -> str:
    if media == "movie":
        crew = _get(f"/movie/{tmdb_id}/credits").get("crew", [])
        directors = [c["name"] for c in crew if c.get("job") == "Director" and c.get("name")]
        return ", ".join(dict.fromkeys(directors[:2]))
    crew = _get(f"/tv/{tmdb_id}/aggregate_credits").get("crew", [])
    for wanted in (_SERIES_DIRECTOR_JOBS, ("Director",)):
        for c in crew:
            if c.get("name") and any(j.get("job") in wanted for j in c.get("jobs") or []):
                return c["name"]
    return ""
