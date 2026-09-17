"""API tests, run against a throwaway SQLite file instead of PostgreSQL —
same SQLAlchemy code path, no database server needed. This is what CI runs
on every push.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_database():
    """Start every test from an empty, freshly-created schema. Unlike the
    pre-rooms schema, a room brings its own page 1 into existence at
    creation time (see crud.create_room), so there's no separate
    "ensure_first_page" startup step to replicate here."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
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
    assert resp.json() == {"code": room["code"]}


def test_unknown_room_code_is_404():
    resp = client.get("/api/rooms/ZZZZZZ")
    assert resp.status_code == 404


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


def test_upload_photo_without_token_is_403(room):
    resp = client.post(
        f"/api/rooms/{room['code']}/uploads",
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    assert resp.status_code == 403
