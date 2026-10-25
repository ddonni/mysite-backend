"""API tests, run against a throwaway SQLite file instead of PostgreSQL —
same SQLAlchemy code path, no database server needed. This is what CI runs
on every push.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app import models
from app.database import SessionLocal
from app.main import FEEDBACK_LIMIT, ROOM_CREATE_LIMIT, ROOM_CREATE_WINDOW, _feedback_posts, _room_creations, app
from app.search_routes import (
    SEARCH_CACHES, BOOK_SEARCH_LIMIT, MUSIC_SEARCH_LIMIT, SCREEN_SEARCH_LIMIT, _book_searches, _music_searches, _screen_searches,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_database():
    """Start every test from an empty, freshly-created schema. Unlike the
    pre-rooms schema, a room brings its own page 1 into existence at
    creation time (see crud.create_room), so there's no separate
    "ensure_first_page" startup step to replicate here."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # Every test in this file hits the same TestClient "IP", so the
    # room-creation rate limit (see app.main) must reset per test too — same
    # reasoning as resetting the database.
    _room_creations.clear()
    _feedback_posts.clear()
    _book_searches.clear()
    _music_searches.clear()
    _screen_searches.clear()
    for c in SEARCH_CACHES:  # 테스트마다 다른 가짜 응답을 쓰니 기억한 검색 결과도 비움
        c.clear()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def room():
    """A fresh room for tests that don't care about room-creation itself."""
    resp = client.post("/api/rooms")
    assert resp.status_code == 200
    return resp.json()  # {"code": ..., "token": ...}


def auth(room):
    return {"X-Room-Token": room["token"]}


def make_stroke(stroke_id="s1"):
    return {
        "id": stroke_id,
        "color": "#2b2b2e",
        "eraser": False,
        "width": 4,
        "author": "test",
        "points": [[0.1, 0.1], [0.2, 0.2]],
    }


def make_record(**overrides):
    body = {"cat": "book", "title": "데미안", "creator": "헤르만 헤세", "rating": 4.5, "memo": "좋았음"}
    body.update(overrides)
    return body


# --- health check ---


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


# --- rooms ---

def test_create_room_returns_code_and_token():
    resp = client.post("/api/rooms")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["code"]) == 6
    assert body["token"]


def test_room_lookup_by_code(room):
    resp = client.get(f"/api/rooms/{room['code']}")
    assert resp.status_code == 200
    assert resp.json() == {"code": room["code"], "theme": "wood", "name": None, "is_public": False}


def test_unknown_room_code_is_404():
    resp = client.get("/api/rooms/ZZZZZZ")
    assert resp.status_code == 404


def test_room_creation_is_rate_limited_per_ip():
    for _ in range(ROOM_CREATE_LIMIT):
        assert client.post("/api/rooms").status_code == 200
    resp = client.post("/api/rooms")
    assert resp.status_code == 429


def test_update_theme(room):
    resp = client.put(f"/api/rooms/{room['code']}/theme", json={"theme": "night"}, headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"theme": "night"}
    assert client.get(f"/api/rooms/{room['code']}").json()["theme"] == "night"


def test_update_theme_requires_owner_token(room):
    resp = client.put(f"/api/rooms/{room['code']}/theme", json={"theme": "night"})
    assert resp.status_code == 403


def test_update_theme_rejects_unknown_value(room):
    resp = client.put(f"/api/rooms/{room['code']}/theme", json={"theme": "neon"}, headers=auth(room))
    assert resp.status_code == 422


def test_update_name_is_trimmed_and_visible_to_visitors(room):
    resp = client.put(f"/api/rooms/{room['code']}/name", json={"name": "  다은이네  "}, headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"name": "다은이네"}
    # 이름은 코드만 있으면 누구나(토큰 없이) 볼 수 있는 공개 값
    assert client.get(f"/api/rooms/{room['code']}").json()["name"] == "다은이네"


def test_update_name_blank_clears_it(room):
    client.put(f"/api/rooms/{room['code']}/name", json={"name": "임시"}, headers=auth(room))
    resp = client.put(f"/api/rooms/{room['code']}/name", json={"name": "   "}, headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"name": None}
    assert client.get(f"/api/rooms/{room['code']}").json()["name"] is None


def test_update_name_requires_owner_token(room):
    resp = client.put(f"/api/rooms/{room['code']}/name", json={"name": "몰래"})
    assert resp.status_code == 403


def test_update_name_rejects_over_20_chars(room):
    resp = client.put(f"/api/rooms/{room['code']}/name", json={"name": "가" * 21}, headers=auth(room))
    assert resp.status_code == 422


# --- 방 둘러보기(공개 목록) ---

def make_public(r):
    resp = client.put(f"/api/rooms/{r['code']}/visibility", json={"public": True}, headers=auth(r))
    assert resp.status_code == 200
    return resp


def test_rooms_start_private_and_owner_can_toggle(room):
    assert client.get(f"/api/rooms/{room['code']}").json()["is_public"] is False
    assert make_public(room).json() == {"is_public": True}
    assert client.get(f"/api/rooms/{room['code']}").json()["is_public"] is True
    resp = client.put(f"/api/rooms/{room['code']}/visibility", json={"public": False}, headers=auth(room))
    assert resp.json() == {"is_public": False}


def test_visibility_requires_owner_token(room):
    resp = client.put(f"/api/rooms/{room['code']}/visibility", json={"public": True})
    assert resp.status_code == 403
    assert client.get(f"/api/rooms/{room['code']}").json()["is_public"] is False


def test_public_list_shows_only_public_rooms_with_records():
    shown = client.post("/api/rooms").json()
    private = client.post("/api/rooms").json()
    empty = client.post("/api/rooms").json()
    for r in (shown, private):
        client.post(f"/api/rooms/{r['code']}/records", json=make_record(), headers=auth(r))
    client.put(f"/api/rooms/{shown['code']}/name", json={"name": "보여줄 방"}, headers=auth(shown))
    make_public(shown)
    make_public(empty)  # 공개여도 기록이 없으면 목록에 안 뜸

    resp = client.get("/api/rooms")
    assert resp.status_code == 200
    rooms = resp.json()
    assert [r["code"] for r in rooms] == [shown["code"]]
    assert rooms[0]["name"] == "보여줄 방"


def test_public_list_counts_by_category_and_hides_secrets(room):
    for cat in ("book", "book", "movie", "music"):
        client.post(f"/api/rooms/{room['code']}/records", json=make_record(cat=cat), headers=auth(room))
    make_public(room)
    (listed,) = client.get("/api/rooms").json()
    assert listed["counts"] == {"book": 2, "anime": 0, "movie": 1, "music": 1}
    assert listed["total"] == 4
    assert "token" not in listed and "google_email" not in listed


def test_public_list_is_ordered_by_latest_record_activity():
    older = client.post("/api/rooms").json()
    newer = client.post("/api/rooms").json()
    client.post(f"/api/rooms/{older['code']}/records", json=make_record(), headers=auth(older))
    rec = client.post(f"/api/rooms/{newer['code']}/records", json=make_record(), headers=auth(newer)).json()
    make_public(older)
    make_public(newer)
    # updated_at을 직접 당겨서 "older가 더 최근에 고쳐짐"을 만들기보다, 시각을
    # 정해두고 확인 — SQLite의 now()는 초 단위라 연달아 만들면 같은 시각일 수 있음.
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        db.query(models.Record).filter(models.Record.id == rec["id"]).update({"updated_at": now - timedelta(days=1)})
        db.query(models.Record).filter(models.Record.id != rec["id"]).update({"updated_at": now})
        db.commit()
    finally:
        db.close()
    assert [r["code"] for r in client.get("/api/rooms").json()] == [older["code"], newer["code"]]


# --- 자동 완성(제목 칸·만든 사람 칸) ---

def suggest(r, q, cat="book", field="title"):
    resp = client.get(f"/api/rooms/{r['code']}/suggestions", params={"cat": cat, "field": field, "q": q})
    assert resp.status_code == 200
    return resp.json()


def values(items):
    return [s["value"] for s in items]


def add(r, **kw):
    client.post(f"/api/rooms/{r['code']}/records", json=make_record(**kw), headers=auth(r))


def test_suggestions_include_my_own_values_marked_mine(room):
    add(room, title="데미안", creator="헤르만 헤세")
    assert suggest(room, "데미") == [{"value": "데미안", "mine": True, "rooms": 1}]
    assert suggest(room, "헤세", field="creator") == [{"value": "헤르만 헤세", "mine": True, "rooms": 1}]
    assert suggest(room, "데미", cat="movie") == []  # 카테고리가 다르면 안 나옴
    assert suggest(room, "") == []


def test_unknown_field_is_rejected(room):
    resp = client.get(f"/api/rooms/{room['code']}/suggestions", params={"cat": "book", "field": "memo", "q": "a"})
    assert resp.status_code == 422  # 한 줄 감상 같은 개인적인 칸은 후보로 안 내줌


def test_other_rooms_values_need_two_public_rooms(room):
    a, b, c = (client.post("/api/rooms").json() for _ in range(3))
    for r in (a, b):
        add(r, title="어린 왕자", creator="생텍쥐페리")
    add(c, title="이상한 제목", creator="장난꾼")
    for r in (a, c):
        make_public(r)
    # b가 비공개라 "어린 왕자"/"생텍쥐페리"는 공개 방 1개뿐 → 안 나옴
    assert suggest(room, "왕자") == []
    assert suggest(room, "생텍", field="creator") == []
    make_public(b)
    assert values(suggest(room, "왕자")) == ["어린 왕자"]
    assert values(suggest(room, "생텍", field="creator")) == ["생텍쥐페리"]
    # 공개 방 하나에만 있는 값은 안 퍼짐
    assert suggest(room, "이상한") == []
    assert suggest(room, "장난", field="creator") == []


def test_private_rooms_values_never_leak(room):
    a, b = (client.post("/api/rooms").json() for _ in range(2))
    for r in (a, b):
        add(r, title="비밀 일기", creator="비밀 작가")
    assert suggest(room, "비밀") == []
    assert suggest(room, "비밀", field="creator") == []


def test_spelling_variants_are_grouped_and_fields_are_independent(room):
    a, b, c = (client.post("/api/rooms").json() for _ in range(3))
    add(a, cat="music", title="Blue", creator="Joni Mitchell")
    add(b, cat="music", title="blue ", creator="joni  mitchell")
    add(c, cat="music", title="Blue", creator="다른 가수")
    for r in (a, b, c):
        make_public(r)
    (s,) = suggest(room, "blu", cat="music")
    assert s["value"] == "Blue" and s["rooms"] == 3 and "creator" not in s  # 제목 후보는 가수를 안 알려줌
    assert values(suggest(room, "joni", cat="music", field="creator")) == ["Joni Mitchell"]


def test_suggestions_prefix_first_then_popularity(room):
    add(room, title="나의 아저씨")
    add(room, title="아저씨")
    assert values(suggest(room, "아저씨")) == ["아저씨", "나의 아저씨"]


# --- pages: read-only vs owner ---

def test_fresh_room_has_one_empty_page(room):
    resp = client.get(f"/api/rooms/{room['code']}/meta")
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}

    resp = client.get(f"/api/rooms/{room['code']}/pages/1")
    assert resp.status_code == 200
    assert resp.json() == {"page_number": 1, "strokes": []}


def test_missing_page_is_404(room):
    resp = client.get(f"/api/rooms/{room['code']}/pages/99")
    assert resp.status_code == 404


def test_save_and_read_back_strokes(room):
    strokes = [make_stroke("a"), make_stroke("b")]
    resp = client.put(f"/api/rooms/{room['code']}/pages/1", json={"strokes": strokes}, headers=auth(room))
    assert resp.status_code == 200
    assert resp.json()["strokes"] == strokes

    resp = client.get(f"/api/rooms/{room['code']}/pages/1")
    assert resp.json()["strokes"] == strokes


def test_write_without_token_is_403(room):
    resp = client.put(f"/api/rooms/{room['code']}/pages/1", json={"strokes": []})
    assert resp.status_code == 403


def test_write_with_wrong_token_is_403(room):
    resp = client.put(
        f"/api/rooms/{room['code']}/pages/1",
        json={"strokes": []},
        headers={"X-Room-Token": "not-the-real-token"},
    )
    assert resp.status_code == 403


def test_add_page_increments_count(room):
    resp = client.post(f"/api/rooms/{room['code']}/pages", headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"page_number": 2, "count": 2}

    resp = client.get(f"/api/rooms/{room['code']}/meta")
    assert resp.json() == {"count": 2}


def test_cannot_delete_the_last_remaining_page(room):
    resp = client.delete(f"/api/rooms/{room['code']}/pages/1", headers=auth(room))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "last_page"


def test_delete_shifts_later_pages_down(room):
    code, headers = room["code"], auth(room)
    for _ in range(3):
        client.post(f"/api/rooms/{code}/pages", headers=headers)
    for n in range(1, 5):
        client.put(f"/api/rooms/{code}/pages/{n}", json={"strokes": [make_stroke(f"page-{n}")]}, headers=headers)

    resp = client.delete(f"/api/rooms/{code}/pages/2", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"count": 3}

    assert client.get(f"/api/rooms/{code}/pages/1").json()["strokes"][0]["id"] == "page-1"
    assert client.get(f"/api/rooms/{code}/pages/2").json()["strokes"][0]["id"] == "page-3"
    assert client.get(f"/api/rooms/{code}/pages/3").json()["strokes"][0]["id"] == "page-4"
    assert client.get(f"/api/rooms/{code}/pages/4").status_code == 404


def test_deleting_the_last_page_just_shrinks_the_count(room):
    code, headers = room["code"], auth(room)
    client.post(f"/api/rooms/{code}/pages", headers=headers)  # now 2 pages
    resp = client.delete(f"/api/rooms/{code}/pages/2", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}
    assert client.get(f"/api/rooms/{code}/pages/2").status_code == 404


def test_rooms_are_isolated():
    room_a = client.post("/api/rooms").json()
    room_b = client.post("/api/rooms").json()

    client.put(
        f"/api/rooms/{room_a['code']}/pages/1",
        json={"strokes": [make_stroke("only-in-a")]},
        headers=auth(room_a),
    )

    # room B's page 1 is unaffected, and A's token doesn't work on B's room
    assert client.get(f"/api/rooms/{room_b['code']}/pages/1").json()["strokes"] == []
    resp = client.put(f"/api/rooms/{room_b['code']}/pages/1", json={"strokes": []}, headers=auth(room_a))
    assert resp.status_code == 403


# --- records ---

def test_create_and_list_records(room):
    resp = client.post(f"/api/rooms/{room['code']}/records", json=make_record(), headers=auth(room))
    assert resp.status_code == 200
    created = resp.json()
    assert created["title"] == "데미안"
    assert created["date"]

    resp = client.get(f"/api/rooms/{room['code']}/records")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_records_require_owner_token(room):
    resp = client.post(f"/api/rooms/{room['code']}/records", json=make_record())
    assert resp.status_code == 403


def test_list_records_filters_by_cat(room):
    headers = auth(room)
    client.post(f"/api/rooms/{room['code']}/records", json=make_record(cat="book"), headers=headers)
    client.post(f"/api/rooms/{room['code']}/records", json=make_record(cat="movie", title="기생충"), headers=headers)

    resp = client.get(f"/api/rooms/{room['code']}/records", params={"cat": "movie"})
    assert resp.status_code == 200
    titles = [r["title"] for r in resp.json()]
    assert titles == ["기생충"]


def test_update_record(room):
    headers = auth(room)
    created = client.post(f"/api/rooms/{room['code']}/records", json=make_record(), headers=headers).json()

    resp = client.put(
        f"/api/rooms/{room['code']}/records/{created['id']}",
        json=make_record(rating=5, memo="다시 읽음"),
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == 5
    assert resp.json()["memo"] == "다시 읽음"


def test_update_missing_record_is_404(room):
    resp = client.put(f"/api/rooms/{room['code']}/records/999", json=make_record(), headers=auth(room))
    assert resp.status_code == 404


def test_delete_record(room):
    headers = auth(room)
    created = client.post(f"/api/rooms/{room['code']}/records", json=make_record(), headers=headers).json()

    resp = client.delete(f"/api/rooms/{room['code']}/records/{created['id']}", headers=headers)
    assert resp.status_code == 200
    assert client.get(f"/api/rooms/{room['code']}/records").json() == []


def test_delete_missing_record_is_404(room):
    resp = client.delete(f"/api/rooms/{room['code']}/records/999", headers=auth(room))
    assert resp.status_code == 404


def _feature(room, record_id, featured=True):
    return client.put(
        f"/api/rooms/{room['code']}/records/{record_id}/feature", json={"featured": featured}, headers=auth(room)
    )


def _create(room, **overrides):
    return client.post(f"/api/rooms/{room['code']}/records", json=make_record(**overrides), headers=auth(room)).json()


def test_feature_up_to_three_per_category(room):
    books = [_create(room, title=f"책{i}") for i in range(4)]
    for b in books[:3]:
        resp = _feature(room, b["id"])
        assert resp.status_code == 200
        assert resp.json()["featured"] is True

    # 4번째는 다른 걸 몰래 끄지 않고 거절함 — 앞의 셋은 그대로 켜져 있어야 함.
    resp = _feature(room, books[3]["id"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "featured_limit"
    records = {r["id"]: r for r in client.get(f"/api/rooms/{room['code']}/records").json()}
    assert [records[b["id"]]["featured"] for b in books] == [True, True, True, False]


def test_feature_limit_is_per_category(room):
    for i in range(3):
        _feature(room, _create(room, title=f"책{i}")["id"])
    movie = _create(room, cat="movie", title="기생충")
    assert _feature(room, movie["id"]).status_code == 200


def test_unfeature_frees_a_slot_and_refeaturing_is_not_counted_twice(room):
    books = [_create(room, title=f"책{i}") for i in range(4)]
    for b in books[:3]:
        _feature(room, b["id"])
    # 이미 켜진 걸 다시 켜는 건 개수를 늘리지 않음.
    assert _feature(room, books[0]["id"]).status_code == 200
    assert _feature(room, books[0]["id"], False).status_code == 200
    assert _feature(room, books[3]["id"]).status_code == 200


def test_feature_record_requires_owner_token(room):
    created = client.post(f"/api/rooms/{room['code']}/records", json=make_record(), headers=auth(room)).json()
    resp = client.put(f"/api/rooms/{room['code']}/records/{created['id']}/feature", json={"featured": True})
    assert resp.status_code == 403


def test_unknown_category_is_rejected(room):
    # 음식(food) 카테고리는 없어졌음 — 예전 프론트가 보내도 저장되면 안 됨.
    resp = client.post(f"/api/rooms/{room['code']}/records", json=make_record(cat="food"), headers=auth(room))
    assert resp.status_code == 422


def test_record_title_is_required(room):
    resp = client.post(f"/api/rooms/{room['code']}/records", json=make_record(title=None), headers=auth(room))
    assert resp.status_code == 422


def _play(room, record_id, playing=True, headers=None):
    return client.put(
        f"/api/rooms/{room['code']}/records/{record_id}/playing",
        json={"playing": playing},
        headers=auth(room) if headers is None else headers,
    )


def test_playing_is_one_song_per_room(room):
    a = _create(room, cat="music", title="노래A")
    b = _create(room, cat="music", title="노래B")
    assert a["playing"] is False  # 기본은 아무 곡도 안 고름(로비가 기본 규칙으로 정함)

    assert _play(room, a["id"]).json()["playing"] is True
    assert _play(room, b["id"]).json()["playing"] is True
    records = {r["id"]: r for r in client.get(f"/api/rooms/{room['code']}/records").json()}
    assert (records[a["id"]]["playing"], records[b["id"]]["playing"]) == (False, True)

    assert _play(room, b["id"], False).json()["playing"] is False


def test_playing_is_per_room():
    room_a = client.post("/api/rooms").json()
    room_b = client.post("/api/rooms").json()
    a = _create(room_a, cat="music", title="A")
    b = _create(room_b, cat="music", title="B")
    _play(room_a, a["id"])
    _play(room_b, b["id"])
    # 다른 방에서 켠 곡이 이 방의 곡을 끄지 않음
    assert client.get(f"/api/rooms/{room_a['code']}/records").json()[0]["playing"] is True


def test_only_music_can_be_playing(room):
    book = _create(room)
    resp = _play(room, book["id"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not_music"


def test_editing_a_song_into_another_category_stops_it_playing(room):
    song = _create(room, cat="music", title="노래")
    _play(room, song["id"])
    resp = client.put(f"/api/rooms/{room['code']}/records/{song['id']}", json=make_record(), headers=auth(room))
    assert resp.json()["playing"] is False


def test_playing_requires_owner_token_and_existing_record(room):
    song = _create(room, cat="music", title="노래")
    assert _play(room, song["id"], headers={}).status_code == 403
    assert _play(room, 999).status_code == 404


def test_feature_missing_record_is_404(room):
    resp = client.put(f"/api/rooms/{room['code']}/records/999/feature", json={"featured": True}, headers=auth(room))
    assert resp.status_code == 404


def test_upload_photo_returns_s3_url(room, monkeypatch):
    async def fake_upload(file):
        return "https://fake-bucket.s3.ap-northeast-2.amazonaws.com/records/fake.jpg"

    monkeypatch.setattr("app.main.storage.upload_photo", fake_upload)

    resp = client.post(
        f"/api/rooms/{room['code']}/uploads",
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
        headers=auth(room),
    )
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://fake-bucket.s3.")


def test_upload_rejects_non_image_types(room):
    # 공개 S3에 올라가는 파일이라 HTML/SVG 같은 건 받지 않음.
    for name, ctype in [("page.html", "text/html"), ("logo.svg", "image/svg+xml")]:
        resp = client.post(
            f"/api/rooms/{room['code']}/uploads",
            files={"file": (name, b"<svg onload=alert(1)>", ctype)},
            headers=auth(room),
        )
        assert resp.status_code == 415


def test_upload_rejects_oversized_file(room, monkeypatch):
    monkeypatch.setattr("app.main.storage.MAX_UPLOAD_BYTES", 10)
    resp = client.post(
        f"/api/rooms/{room['code']}/uploads",
        files={"file": ("photo.jpg", b"x" * 11, "image/jpeg")},
        headers=auth(room),
    )
    assert resp.status_code == 413


def test_upload_key_extension_comes_from_type_not_filename(monkeypatch):
    import asyncio
    from io import BytesIO

    from starlette.datastructures import Headers, UploadFile

    from app import storage

    put = {}

    class FakeS3:
        def put_object(self, **kw):
            put.update(kw)

    monkeypatch.setattr(storage, "_client", FakeS3())
    monkeypatch.setattr(storage, "S3_BUCKET_NAME", "bucket")
    f = UploadFile(BytesIO(b"img"), filename="evil.html", headers=Headers({"content-type": "image/png"}))
    url = asyncio.run(storage.upload_photo(f))
    assert url.endswith(".png")
    assert put["ContentType"] == "image/png"


def test_record_date_is_korean_today(room, monkeypatch):
    # 서버가 UTC여도 한국 날짜로 찍혀야 함 — UTC 9월 27일 16시 = 한국 28일 새벽 1시.
    from datetime import datetime as real_datetime

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 27, 16, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("app.crud.datetime", FrozenDatetime)
    created = client.post(f"/api/rooms/{room['code']}/records", json=make_record(), headers=auth(room)).json()
    assert created["date"] == "2026-09-28"


def test_rate_limit_forgets_ips_after_the_window():
    stale = datetime.now(timezone.utc) - ROOM_CREATE_WINDOW - timedelta(minutes=1)
    _room_creations["203.0.113.9"] = [stale]
    client.post("/api/rooms")
    assert "203.0.113.9" not in _room_creations


def test_upload_photo_without_token_is_403(room):
    resp = client.post(
        f"/api/rooms/{room['code']}/uploads",
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    assert resp.status_code == 403


# --- google account link/recover (POST /api/auth/google) ---

def fake_google_claims(sub="google-user-1"):
    return {"sub": sub, "email": "test@example.com"}


def test_google_auth_links_current_room_first_time(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())

    resp = client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": room["code"], "current_token": room["token"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"code": room["code"], "token": room["token"], "linked_new": True, "current_empty": None}


def test_google_auth_recovers_already_linked_room(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": room["code"], "current_token": room["token"],
    })

    # 새 기기 시나리오: current_code/current_token 없이 구글 계정만으로 복구.
    resp = client.post("/api/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"code": room["code"], "token": room["token"], "linked_new": False, "current_empty": None}


def link_google(r):
    client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": r["code"], "current_token": r["token"],
    })


def test_google_auth_recover_from_new_empty_room_says_current_empty(room, monkeypatch):
    # 새 브라우저 시나리오: 로비를 여는 순간 빈 방이 저절로 생긴 뒤 로그인함.
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    link_google(room)
    fresh = client.post("/api/rooms").json()

    resp = client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": fresh["code"], "current_token": fresh["token"],
    })
    assert resp.json() == {"code": room["code"], "token": room["token"], "linked_new": False, "current_empty": True}


def test_google_auth_recover_from_room_with_records_says_not_empty(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    link_google(room)
    other = client.post("/api/rooms").json()
    client.post(f"/api/rooms/{other['code']}/records", json=make_record(), headers=auth(other))

    resp = client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": other["code"], "current_token": other["token"],
    })
    assert resp.json()["current_empty"] is False


def test_google_auth_recover_from_room_with_drawing_says_not_empty(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    link_google(room)
    other = client.post("/api/rooms").json()
    client.put(f"/api/rooms/{other['code']}/pages/1", json={"strokes": [make_stroke("a")]}, headers=auth(other))

    resp = client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": other["code"], "current_token": other["token"],
    })
    assert resp.json()["current_empty"] is False


def test_google_auth_current_empty_needs_the_real_current_token(room, monkeypatch):
    # 남의 방 코드만 알아서는 그 방이 비었는지 알아낼 수 없어야 함.
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    link_google(room)
    other = client.post("/api/rooms").json()

    resp = client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": other["code"], "current_token": "wrong",
    })
    assert resp.json()["current_empty"] is None


def test_google_auth_invalid_token_is_400(monkeypatch):
    def raise_invalid(t):
        raise ValueError("bad token")
    monkeypatch.setattr("app.main.google_auth.verify_id_token", raise_invalid)

    resp = client.post("/api/auth/google", json={"id_token": "not-a-real-token"})
    assert resp.status_code == 400


def test_google_auth_no_link_and_no_current_room_is_404(monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    resp = client.post("/api/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 404


def test_google_auth_wrong_current_token_is_404(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    resp = client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": room["code"], "current_token": "not-the-real-token",
    })
    assert resp.status_code == 404


# --- google link status (GET /api/rooms/{code}/google) ---

def test_google_status_unlinked_by_default(room):
    resp = client.get(f"/api/rooms/{room['code']}/google", headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"linked": False, "email": None}


def test_google_status_shows_linked_email(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": room["code"], "current_token": room["token"],
    })

    resp = client.get(f"/api/rooms/{room['code']}/google", headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"linked": True, "email": "test@example.com"}


def test_google_status_requires_owner_token(room):
    resp = client.get(f"/api/rooms/{room['code']}/google")
    assert resp.status_code == 403


# --- google unlink (DELETE /api/rooms/{code}/google) ---

def test_google_unlink_clears_the_link_and_stops_recovery(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": room["code"], "current_token": room["token"],
    })

    resp = client.delete(f"/api/rooms/{room['code']}/google", headers=auth(room))
    assert resp.status_code == 200
    assert resp.json() == {"linked": False, "email": None}
    assert client.get(f"/api/rooms/{room['code']}/google", headers=auth(room)).json()["linked"] is False

    # 해제 뒤엔 그 구글 계정만으로는 방을 되찾을 수 없음
    assert client.post("/api/auth/google", json={"id_token": "fake"}).status_code == 404


def test_google_unlink_requires_owner_token(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    client.post("/api/auth/google", json={
        "id_token": "fake", "current_code": room["code"], "current_token": room["token"],
    })

    assert client.delete(f"/api/rooms/{room['code']}/google").status_code == 403
    assert client.get(f"/api/rooms/{room['code']}/google", headers=auth(room)).json()["linked"] is True


def test_google_relink_after_unlink_works(room, monkeypatch):
    monkeypatch.setattr("app.main.google_auth.verify_id_token", lambda t: fake_google_claims())
    link = {"id_token": "fake", "current_code": room["code"], "current_token": room["token"]}
    client.post("/api/auth/google", json=link)
    client.delete(f"/api/rooms/{room['code']}/google", headers=auth(room))

    resp = client.post("/api/auth/google", json=link)
    assert resp.status_code == 200
    assert resp.json()["linked_new"] is True


# --- feedback (POST /api/feedback) ---

def saved_feedback():
    db = SessionLocal()
    try:
        return db.query(models.Feedback).all()
    finally:
        db.close()


def test_feedback_is_saved_without_a_token():
    resp = client.post("/api/feedback", json={
        "message": "  캔버스 색을 RGB로 고르고 싶어요  ", "name": "  민기 ", "page": "/mysite/sketchbook", "room_code": "TTHB8N",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    [fb] = saved_feedback()
    assert (fb.message, fb.name, fb.page, fb.room_code) == ("캔버스 색을 RGB로 고르고 싶어요", "민기", "/mysite/sketchbook", "TTHB8N")
    assert fb.created_at is not None


def test_feedback_blank_name_is_stored_as_none():
    client.post("/api/feedback", json={"message": "좋아요", "name": "   "})
    [fb] = saved_feedback()
    assert fb.name is None


def test_feedback_rejects_blank_or_too_long_message():
    assert client.post("/api/feedback", json={"message": "   "}).status_code == 422
    assert client.post("/api/feedback", json={"message": "가" * 1001}).status_code == 422
    assert saved_feedback() == []


def test_feedback_is_rate_limited_per_ip():
    for _ in range(FEEDBACK_LIMIT):
        assert client.post("/api/feedback", json={"message": "hi"}).status_code == 200
    resp = client.post("/api/feedback", json={"message": "hi"})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "too many feedback, try again later"
    # 방 만들기 제한과는 따로 셈
    assert client.post("/api/rooms").status_code == 200


# --- book search (GET /api/search/book) + cover relay (GET /api/cover) ---

KAKAO_DEMIAN = {
    "documents": [
        {
            "title": "데미안(양장본)(1919년 초판본 표지 디자인)",
            "authors": ["헤르만 헤세"],
            "translators": ["전영애"],
            "publisher": "민음사",
            "datetime": "2009-01-20T00:00:00.000+09:00",
            "thumbnail": "https://search1.kakaocdn.net/thumb/R120x174.q85/?fname=http%3A%2F%2Ft1.daumcdn.net%2Flbook%2Fimage%2F540810%3Ftimestamp%3D20261023110741",
        },
        {
            "title": "데미안: 에밀 싱클레어의 청소년 시절 이야기",
            "authors": ["헤르만 헤세", "A", "B"],
            "publisher": "코너스톤",
            "datetime": "",
            "thumbnail": "",
        },
    ],
    "meta": {"total_count": 2},
}


@pytest.fixture
def kakao(monkeypatch):
    """Kakao 키가 있는 것처럼 하고, 실제 호출 대신 받은 query를 기록함."""
    monkeypatch.setenv("KAKAO_REST_KEY", "test-key")
    calls = []

    def fake_get(query, key, target=None):
        calls.append((query, key, target))
        return KAKAO_DEMIAN
    monkeypatch.setattr("app.book_search._kakao_get", fake_get)
    return calls


def test_book_search_trims_kakao_results_for_the_form(kakao):
    resp = client.get("/api/search/book", params={"q": " 데미안 "})
    assert resp.status_code == 200
    first, second = resp.json()
    assert kakao == [("데미안", "test-key", None)]
    assert first == {
        "title": "데미안",
        "full_title": "데미안(양장본)(1919년 초판본 표지 디자인)",
        "creator": "헤르만 헤세",
        "publisher": "민음사",
        "year": "2009",
        # 120x174 썸네일 대신 fname= 안의 원본, https로
        "cover": "https://t1.daumcdn.net/lbook/image/540810?timestamp=20261023110741",
    }
    assert second["title"] == "데미안"
    assert second["creator"] == "헤르만 헤세, A 외"
    assert second["cover"] is None and second["year"] == ""


def test_book_search_by_author_asks_kakao_for_person(kakao):
    assert client.get("/api/search/book", params={"q": "한강", "by": "author"}).status_code == 200
    assert kakao == [("한강", "test-key", "person")]


def test_book_search_unknown_by_is_422(kakao):
    assert client.get("/api/search/book", params={"q": "한강", "by": "publisher"}).status_code == 422
    assert kakao == []


def test_clean_title_keeps_volume_numbers_and_bare_colons():
    from app.book_search import clean_title
    assert clean_title("해리 포터와 마법사의 돌(1)") == "해리 포터와 마법사의 돌(1)"
    assert clean_title("나미야 잡화점의 기적(양장본)") == "나미야 잡화점의 기적"
    assert clean_title("Re:제로부터 시작하는 이세계 생활 1") == "Re:제로부터 시작하는 이세계 생활 1"


def test_book_search_without_key_is_503(monkeypatch):
    monkeypatch.delenv("KAKAO_REST_KEY", raising=False)
    resp = client.get("/api/search/book", params={"q": "데미안"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "book search unavailable"


def test_book_search_kakao_failure_is_503(monkeypatch):
    import requests
    monkeypatch.setenv("KAKAO_REST_KEY", "test-key")

    def boom(query, key, target=None):
        raise requests.ConnectionError("down")
    monkeypatch.setattr("app.book_search._kakao_get", boom)
    assert client.get("/api/search/book", params={"q": "데미안"}).status_code == 503


def test_book_search_empty_query_is_422(kakao):
    assert client.get("/api/search/book", params={"q": ""}).status_code == 422


def test_book_search_rate_limit(kakao):
    _book_searches["testclient"] = [datetime.now(timezone.utc)] * BOOK_SEARCH_LIMIT
    resp = client.get("/api/search/book", params={"q": "데미안"})
    assert resp.status_code == 429
    assert kakao == []


def test_cover_relay_returns_image_with_long_cache(monkeypatch):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"
    monkeypatch.setattr("app.book_search._fetch_image", fake_fetch)

    resp = client.get("/api/cover", params={"url": "http://t1.daumcdn.net/lbook/image/540810?timestamp=1"})
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8jpeg"
    assert resp.headers["content-type"] == "image/jpeg"
    assert "max-age=" in resp.headers["cache-control"]
    assert fetched == ["https://t1.daumcdn.net/lbook/image/540810?timestamp=1"]  # 항상 https로 받음


@pytest.mark.parametrize("url", [
    "https://example.com/a.jpg",
    "https://t1.daumcdn.net.evil.com/a.jpg",
    "file:///etc/passwd",
    "http://127.0.0.1:8000/health",
    "not a url",
])
def test_cover_relay_refuses_other_hosts(monkeypatch, url):
    def never(u):
        raise AssertionError("must not fetch " + u)
    monkeypatch.setattr("app.book_search._fetch_image", never)
    resp = client.get("/api/cover", params={"url": url})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cover host not allowed"


def test_cover_relay_upstream_failure_is_502(monkeypatch):
    from app.book_search import CoverFetchFailed

    def fail(u):
        raise CoverFetchFailed("status 404")
    monkeypatch.setattr("app.book_search._fetch_image", fail)
    resp = client.get("/api/cover", params={"url": "https://t1.daumcdn.net/lbook/image/1"})
    assert resp.status_code == 502


# --- music search (GET /api/search/music) ---

YTM_RESULTS = [
    {"resultType": "episode", "title": "[클립] 꽃갈피", "artists": []},
    {"resultType": "album", "title": "꽃갈피", "type": "EP", "year": "2014",
     "artists": [{"name": "아이유(IU)"}],
     "thumbnails": [{"url": "https://yt3.googleusercontent.com/a=w60", "width": 60},
                    {"url": "https://yt3.googleusercontent.com/a=w544", "width": 544}]},
    {"resultType": "song", "title": "나의 옛날이야기", "album": {"name": "꽃갈피"},
     "artists": [{"name": "아이유(IU)"}], "thumbnails": []},
    {"resultType": "artist", "artist": "아이유"},
]


@pytest.fixture
def ytm(monkeypatch):
    """유튜브 대신 받은 (query, auth_file)을 기록하고 YTM_RESULTS를 돌려줌."""
    monkeypatch.delenv("YTMUSIC_AUTH_FILE", raising=False)
    calls = []

    def fake_search(query, auth_file):
        calls.append((query, auth_file))
        return YTM_RESULTS
    monkeypatch.setattr("app.music_search._search", fake_search)
    return calls


def test_music_search_keeps_albums_and_songs_only(ytm):
    resp = client.get("/api/search/music", params={"q": " 아이유 꽃갈피 "})
    assert resp.status_code == 200
    assert ytm == [("아이유 꽃갈피", None)]
    assert resp.json() == [
        {"album": "꽃갈피", "song": None, "creator": "아이유(IU)", "kind": "EP", "year": "2014",
         "cover": "https://yt3.googleusercontent.com/a=w544"},  # 가장 큰 썸네일
        {"album": "꽃갈피", "song": "나의 옛날이야기", "creator": "아이유(IU)", "kind": "노래", "year": "",
         "cover": None},
    ]


def test_music_search_falls_back_to_anonymous_when_signed_in_search_fails(monkeypatch):
    monkeypatch.setenv("YTMUSIC_AUTH_FILE", "/etc/mysite/ytmusic.json")
    calls = []

    def fake_search(query, auth_file):
        calls.append(auth_file)
        if auth_file:
            raise Exception("cookies expired")
        return YTM_RESULTS
    monkeypatch.setattr("app.music_search._search", fake_search)

    resp = client.get("/api/search/music", params={"q": "꽃갈피"})
    assert resp.status_code == 200
    assert calls == ["/etc/mysite/ytmusic.json", None]
    assert len(resp.json()) == 2


def test_music_search_youtube_failure_is_503(monkeypatch):
    def boom(query, auth_file):
        raise KeyError("contents")  # 유튜브 응답 형식이 바뀌어 ytmusicapi가 깨진 경우
    monkeypatch.setattr("app.music_search._search", boom)
    resp = client.get("/api/search/music", params={"q": "꽃갈피"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "music search unavailable"


def test_music_search_rate_limit(ytm):
    _music_searches["testclient"] = [datetime.now(timezone.utc)] * MUSIC_SEARCH_LIMIT
    assert client.get("/api/search/music", params={"q": "꽃갈피"}).status_code == 429
    assert ytm == []
    # 책 검색 한도와는 따로 셈
    assert "testclient" not in _book_searches


# --- movie/anime search (GET /api/search/screen, /api/search/screen/creator) ---

TMDB_MULTI = {"results": [
    {"media_type": "tv", "id": 1429, "name": "진격의 거인", "original_name": "進撃の巨人",
     "first_air_date": "2013-04-07", "genre_ids": [16, 10765], "poster_path": "/a.jpg"},
    {"media_type": "movie", "id": 99, "title": "진격의 거인 실사판", "original_title": "進撃の巨人",
     "release_date": "2015-08-01", "genre_ids": [28], "poster_path": "/b.jpg"},  # 애니 아님
    {"media_type": "person", "id": 5, "name": "진격"},
    {"media_type": "movie", "id": 4935, "title": "하울의 움직이는 성", "original_title": "ハウルの動く城",
     "release_date": "2004-11-19", "genre_ids": [14, 16], "poster_path": None},
]}
TMDB_MOVIE = {"results": [
    {"id": 157336, "title": "인터스텔라", "original_title": "Interstellar", "release_date": "2014-11-05",
     "poster_path": "/gD.jpg"},
    {"id": 496243, "title": "기생충", "original_title": "기생충", "release_date": "2019-05-30",
     "poster_path": "/jj.jpg"},
]}


@pytest.fixture
def tmdb(monkeypatch):
    """TMDB 토큰이 있는 것처럼 하고, (path, params)를 기록하며 path별 가짜 응답을 줌."""
    monkeypatch.setenv("TMDB_TOKEN", "test-token")
    calls = []
    responses = {
        "/search/multi": TMDB_MULTI,
        "/search/movie": TMDB_MOVIE,
        "/movie/496243/credits": {"crew": [
            {"job": "Producer", "name": "곽신애"}, {"job": "Director", "name": "봉준호"}]},
        "/tv/1429/aggregate_credits": {"crew": [
            {"name": "회차 연출", "jobs": [{"job": "Director"}]},
            {"name": "아라키 테츠로", "jobs": [{"job": "Series Director"}]}]},
        "/search/person": {"results": [
            {"id": 104522, "name": "미야자키 아오이", "known_for_department": "Acting"},
            {"id": 608, "name": "미야자키 하야오", "known_for_department": "Directing"}]},
        "/person/608/combined_credits": {"crew": [
            {"media_type": "movie", "id": 1, "title": "단편", "job": "Director", "genre_ids": [16], "vote_count": 3},
            {"media_type": "movie", "id": 129, "title": "센과 치히로의 행방불명", "job": "Director",
             "genre_ids": [16, 14], "vote_count": 17000, "release_date": "2001-07-20", "poster_path": "/s.jpg"},
            {"media_type": "movie", "id": 129, "title": "센과 치히로의 행방불명", "job": "Screenplay", "genre_ids": [16]},
            {"media_type": "movie", "id": 7, "title": "실사 다큐", "job": "Director", "genre_ids": [99], "vote_count": 50},
            {"media_type": "tv", "id": 8, "name": "미래소년 코난", "job": "Director", "genre_ids": [16], "vote_count": 400},
            {"media_type": "tv", "id": 8, "name": "미래소년 코난", "job": "Director", "genre_ids": [16], "vote_count": 400},
        ]},
        "/tv/2/aggregate_credits": {"crew": [
            {"name": "각본가", "jobs": [{"job": "Writer"}]},
            {"name": "와타나베 신이치로", "jobs": [{"job": "Director"}]}]},
    }

    def fake_get(path, params, token):
        calls.append((path, params, token))
        return responses.get(path, {"crew": []})
    monkeypatch.setattr("app.screen_search._tmdb_get", fake_get)
    return calls


def test_screen_search_movie(tmdb):
    resp = client.get("/api/search/screen", params={"q": " 인터스텔라 ", "kind": "movie"})
    assert resp.status_code == 200
    assert tmdb[0][:1] == ("/search/movie",) and tmdb[0][1]["query"] == "인터스텔라" and tmdb[0][2] == "test-token"
    assert resp.json() == [
        {"id": 157336, "media": "movie", "title": "인터스텔라", "original_title": "Interstellar", "year": "2014",
         "cover": "https://image.tmdb.org/t/p/w342/gD.jpg", "creator": ""},
        # 원제가 한국어 제목과 같으면 비워 둠(목록에 같은 말을 두 번 안 씀)
        {"id": 496243, "media": "movie", "title": "기생충", "original_title": "", "year": "2019",
         "cover": "https://image.tmdb.org/t/p/w342/jj.jpg", "creator": ""},
    ]


def test_screen_search_anime_keeps_animation_movies_and_tv_only(tmdb):
    resp = client.get("/api/search/screen", params={"q": "진격", "kind": "anime"})
    assert tmdb[0][0] == "/search/multi"
    assert [(r["media"], r["title"], r["cover"]) for r in resp.json()] == [
        ("tv", "진격의 거인", "https://image.tmdb.org/t/p/w342/a.jpg"),
        ("movie", "하울의 움직이는 성", None),
    ]


def test_screen_search_without_token_is_503(monkeypatch):
    monkeypatch.delenv("TMDB_TOKEN", raising=False)
    resp = client.get("/api/search/screen", params={"q": "기생충"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "screen search unavailable"


def test_screen_search_unknown_kind_is_422(tmdb):
    assert client.get("/api/search/screen", params={"q": "기생충", "kind": "drama"}).status_code == 422


def test_screen_creator_movie_director(tmdb):
    resp = client.get("/api/search/screen/creator", params={"media": "movie", "id": 496243})
    assert resp.json() == {"creator": "봉준호"}


def test_screen_creator_tv_prefers_series_director(tmdb):
    resp = client.get("/api/search/screen/creator", params={"media": "tv", "id": 1429})
    assert resp.json() == {"creator": "아라키 테츠로"}


def test_screen_creator_tv_falls_back_to_first_director(tmdb):
    resp = client.get("/api/search/screen/creator", params={"media": "tv", "id": 2})
    assert resp.json() == {"creator": "와타나베 신이치로"}


def test_screen_creator_unknown_is_empty(tmdb):
    assert client.get("/api/search/screen/creator", params={"media": "movie", "id": 1}).json() == {"creator": ""}


def test_screen_search_rate_limit(tmdb):
    _screen_searches["testclient"] = [datetime.now(timezone.utc)] * SCREEN_SEARCH_LIMIT
    assert client.get("/api/search/screen", params={"q": "기생충"}).status_code == 429
    assert client.get("/api/search/screen/creator", params={"media": "movie", "id": 1}).status_code == 429
    assert tmdb == []


def test_screen_search_by_director_lists_their_works_most_known_first(tmdb):
    resp = client.get("/api/search/screen", params={"q": "미야자키", "kind": "anime", "by": "director"})
    assert resp.status_code == 200
    assert [c[0] for c in tmdb] == ["/search/person", "/person/608/combined_credits"]  # 배우 말고 감독
    got = [(r["media"], r["title"], r["creator"]) for r in resp.json()]
    # 애니만, 연출만(각본 줄 빠짐), TV 회차 중복 빠짐, 평가 수 많은 순
    assert got == [
        ("movie", "센과 치히로의 행방불명", "미야자키 하야오"),
        ("tv", "미래소년 코난", "미야자키 하야오"),
        ("movie", "단편", "미야자키 하야오"),
    ]


def test_screen_search_by_director_movie_tab_keeps_movies_only(tmdb):
    resp = client.get("/api/search/screen", params={"q": "미야자키", "kind": "movie", "by": "director"})
    assert [r["title"] for r in resp.json()] == ["센과 치히로의 행방불명", "실사 다큐", "단편"]


def test_screen_search_by_director_no_director_found_is_empty(tmdb, monkeypatch):
    def only_actors(path, params, token):
        return {"results": [{"id": 1, "name": "배우", "known_for_department": "Acting"}]}
    monkeypatch.setattr("app.screen_search._tmdb_get", only_actors)
    assert client.get("/api/search/screen", params={"q": "배우", "by": "director"}).json() == []


ARTIST_PAGE = {
    "name": "검정치마",
    "albums": {"results": [
        {"title": "TEAM BABY", "year": "2017", "thumbnails": [{"url": "https://yt3.googleusercontent.com/tb=w544"}]},
        {"title": "THIRSTY", "thumbnails": []},
    ]},
    "singles": {"results": [{"title": "Fruit Fly", "thumbnails": [{"url": "https://yt3.googleusercontent.com/ff=w544"}]}]},
}


def test_music_search_by_artist_lists_the_artists_albums_then_singles(monkeypatch):
    calls = []
    monkeypatch.setattr("app.music_search._search", lambda q, auth: [
        {"resultType": "artist", "artist": None},  # 맨 위 가수 카드는 이름·id를 못 읽을 때가 있음
        {"resultType": "album", "title": "TEAM BABY", "artists": [{"name": "검정치마", "id": "UCR90"}]},
    ])

    def fake_artist(channel):
        calls.append(channel)
        return ARTIST_PAGE
    monkeypatch.setattr("app.music_search._artist", fake_artist)

    resp = client.get("/api/search/music", params={"q": "검정치마", "by": "artist"})
    assert resp.status_code == 200
    assert calls == ["UCR90"]  # 앨범 결과에 붙은 가수 id로 가수 페이지를 엶
    assert [(r["album"], r["kind"], r["creator"]) for r in resp.json()] == [
        ("TEAM BABY", "앨범", "검정치마"), ("THIRSTY", "앨범", "검정치마"), ("Fruit Fly", "싱글", "검정치마"),
    ]
    assert resp.json()[0]["year"] == "2017" and resp.json()[1]["cover"] is None


def test_music_search_by_artist_falls_back_to_plain_results(monkeypatch):
    monkeypatch.setattr("app.music_search._search", lambda q, auth: YTM_RESULTS)

    def broken(channel):
        raise KeyError("contents")
    monkeypatch.setattr("app.music_search._artist", broken)
    resp = client.get("/api/search/music", params={"q": "아이유", "by": "artist"})
    assert [r["album"] for r in resp.json()] == ["꽃갈피", "꽃갈피"]  # 일반 검색 결과로 대신


def test_music_search_unknown_by_is_422(ytm):
    assert client.get("/api/search/music", params={"q": "x", "by": "album"}).status_code == 422


# --- search cache (app/cache.py) ---

def test_same_book_search_is_answered_from_cache(kakao):
    client.get("/api/search/book", params={"q": "데미안"})
    client.get("/api/search/book", params={"q": " 데미안 "})  # 앞뒤 공백만 다른 것도 같은 검색
    client.get("/api/search/book", params={"q": "데미안", "by": "author"})  # 작가 검색은 따로
    assert [c[2] for c in kakao] == [None, "person"]


def test_failed_search_is_not_cached(monkeypatch):
    import requests
    monkeypatch.setenv("KAKAO_REST_KEY", "test-key")
    calls = []

    def flaky(query, key, target=None):
        calls.append(query)
        if len(calls) == 1:
            raise requests.ConnectionError("down")
        return KAKAO_DEMIAN
    monkeypatch.setattr("app.book_search._kakao_get", flaky)
    assert client.get("/api/search/book", params={"q": "데미안"}).status_code == 503
    assert client.get("/api/search/book", params={"q": "데미안"}).status_code == 200  # 다시 물어봄
    assert len(calls) == 2


def test_cache_expires_and_drops_oldest(monkeypatch):
    from app.cache import TTLCache
    now = [100.0]
    monkeypatch.setattr("app.cache.time.monotonic", lambda: now[0])
    c = TTLCache(ttl_seconds=10, max_entries=2)
    n = []
    get = lambda k: c.get_or_set(k, lambda: n.append(k) or k)
    get("a"); get("a")
    assert n == ["a"]
    now[0] += 11
    get("a")  # 10초 지나면 다시 계산
    assert n == ["a", "a"]
    get("b"); get("c")  # 최대 2개 — 가장 오래된 a가 빠짐
    get("a")
    assert n == ["a", "a", "b", "c", "a"]
