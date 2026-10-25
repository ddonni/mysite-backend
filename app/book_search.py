"""Book lookup for the "새 기록" form (Kakao/Daum book search) and a small
image relay for the covers it returns.

Kept in its own module, like google_auth.py, so tests can monkeypatch the
two network calls (_kakao_get, _fetch_image) instead of hitting Kakao.

Why a cover relay at all: the frontend loads every record photo with
crossOrigin="anonymous" (the lobby draws them into WebGL textures, and the
library list matches that so the browser cache doesn't mix CORS and non-CORS
copies). Kakao's image servers send no Access-Control-Allow-Origin, so those
loads fail. GET /api/cover fetches the image server-side and returns it
through our own CORS headers. Only Kakao's cover hosts are allowed — an
open relay would let anyone use this server as a proxy.
"""
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

KAKAO_BOOK_URL = "https://dapi.kakao.com/v3/search/book"
SEARCH_SIZE = 8

COVER_HOSTS = {"t1.daumcdn.net", "search1.kakaocdn.net"}
COVER_MAX_BYTES = 3 * 1024 * 1024
TIMEOUT = 5


class SearchUnavailable(Exception):
    """No KAKAO_REST_KEY configured, or Kakao didn't answer properly."""


class CoverNotAllowed(Exception):
    pass


class CoverFetchFailed(Exception):
    pass


# --- search ---

def _kakao_get(query: str, key: str, target: Optional[str] = None) -> dict:
    params = {"query": query, "size": SEARCH_SIZE}
    if target:
        params["target"] = target  # "person" = 작가·역자 이름에서만 찾음
    resp = requests.get(
        KAKAO_BOOK_URL,
        params=params,
        headers={"Authorization": f"KakaoAK {key}"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# 제목 뒤에 붙는 판본 표시 — "(양장본)", "(1919년 초판본 표지 디자인)" 같은 것.
# 숫자만 든 괄호는 권수일 수 있어서("해리 포터(1)") 남김.
_TRAILING_PAREN = re.compile(r"\s*\((?!\s*\d+\s*(권)?\s*\))[^()]*\)\s*$")


def clean_title(title: str) -> str:
    """"데미안(양장본)(1919년 초판본 표지 디자인)" → "데미안",
    "데미안: 에밀 싱클레어의 청소년 시절 이야기" → "데미안". The form stays
    editable, so this only has to be right most of the time."""
    t = (title or "").strip()
    while True:
        stripped = _TRAILING_PAREN.sub("", t)
        if stripped == t:
            break
        t = stripped
    # 부제는 ": " 뒤 — 띄어쓰기 없는 콜론("Re:제로")은 제목의 일부라 안 자름.
    if ": " in t:
        head = t.split(": ", 1)[0].strip()
        if head:
            t = head
    return t or (title or "").strip()


def cover_from_thumbnail(thumbnail: str) -> Optional[str]:
    """Kakao's thumbnail is a 120x174 resize whose fname= parameter holds the
    original (about 400x700) — the original reads much better as a framed
    cover in the lobby. Falls back to the thumbnail itself."""
    if not thumbnail:
        return None
    fname = parse_qs(urlparse(thumbnail).query).get("fname", [None])[0]
    if fname:
        orig = urlparse(fname)
        if orig.hostname in COVER_HOSTS:
            return orig._replace(scheme="https").geturl()
    return thumbnail


def search_books(query: str, by_author: bool = False) -> List[dict]:
    """by_author: 작가 칸에서 검색할 때 — 제목·내용이 아니라 작가 이름으로만 찾음
    ("한강"이 제목에 든 책 대신 한강 작가의 책)."""
    key = os.environ.get("KAKAO_REST_KEY", "").strip()
    if not key:
        raise SearchUnavailable("KAKAO_REST_KEY is not set")
    try:
        data = _kakao_get(query, key, "person" if by_author else None)
    except (requests.RequestException, ValueError) as e:
        raise SearchUnavailable(str(e))

    out = []
    for doc in data.get("documents", []):
        authors = [a for a in doc.get("authors") or [] if a]
        creator = ", ".join(authors[:2]) + (" 외" if len(authors) > 2 else "")
        out.append({
            "title": clean_title(doc.get("title", "")),
            "full_title": (doc.get("title") or "").strip(),
            "creator": creator,
            "publisher": (doc.get("publisher") or "").strip(),
            "year": (doc.get("datetime") or "")[:4],
            "cover": cover_from_thumbnail(doc.get("thumbnail") or ""),
        })
    return out


# --- cover relay ---

def _fetch_image(url: str) -> Tuple[bytes, str]:
    # allow_redirects=False: 허용한 서버가 다른 곳으로 넘겨도 따라가지 않음
    # (허용 목록을 우회하는 통로가 되지 않게).
    resp = requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False)
    if resp.status_code != 200:
        raise CoverFetchFailed(f"status {resp.status_code}")
    content_type = resp.headers.get("content-type", "").split(";")[0].strip()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise CoverFetchFailed(f"not an image: {content_type}")
    body = b""
    for chunk in resp.iter_content(64 * 1024):
        body += chunk
        if len(body) > COVER_MAX_BYTES:
            raise CoverFetchFailed("too large")
    return body, content_type


def fetch_cover(url: str) -> Tuple[bytes, str]:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or parsed.hostname not in COVER_HOSTS:
        raise CoverNotAllowed(url)
    try:
        return _fetch_image(parsed._replace(scheme="https").geturl())
    except requests.RequestException as e:
        raise CoverFetchFailed(str(e))
