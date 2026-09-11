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
    """Start every test from an empty, freshly-created schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def make_stroke(stroke_id="s1"):
    return {"id": stroke_id, "c": "#2b2b2e", "e": False, "s": 4, "by": "test", "p": [[0.1, 0.1], [0.2, 0.2]]}


def test_fresh_db_has_one_empty_page():
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}

    resp = client.get("/api/pages/1")
    assert resp.status_code == 200
    assert resp.json() == {"page_number": 1, "strokes": []}


def test_missing_page_is_404():
    resp = client.get("/api/pages/99")
    assert resp.status_code == 404


def test_save_and_read_back_strokes():
    strokes = [make_stroke("a"), make_stroke("b")]
    resp = client.put("/api/pages/1", json={"strokes": strokes})
    assert resp.status_code == 200
    assert resp.json()["strokes"] == strokes

    resp = client.get("/api/pages/1")
    assert resp.json()["strokes"] == strokes


def test_add_page_increments_count():
    resp = client.post("/api/pages")
    assert resp.status_code == 200
    assert resp.json() == {"page_number": 2, "count": 2}

    resp = client.get("/api/meta")
    assert resp.json() == {"count": 2}


def test_cannot_delete_the_last_remaining_page():
    resp = client.delete("/api/pages/1")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "last_page"


def test_delete_shifts_later_pages_down():
    # build 4 pages, each with a distinct stroke so we can tell them apart
    for _ in range(3):
        client.post("/api/pages")
    for n in range(1, 5):
        client.put(f"/api/pages/{n}", json={"strokes": [make_stroke(f"page-{n}")]})

    # delete page 2 -> old page 3 becomes new page 2, old page 4 becomes new page 3
    resp = client.delete("/api/pages/2")
    assert resp.status_code == 200
    assert resp.json() == {"count": 3}

    assert client.get("/api/pages/1").json()["strokes"][0]["id"] == "page-1"
    assert client.get("/api/pages/2").json()["strokes"][0]["id"] == "page-3"
    assert client.get("/api/pages/3").json()["strokes"][0]["id"] == "page-4"
    assert client.get("/api/pages/4").status_code == 404


def test_deleting_the_last_page_just_shrinks_the_count():
    client.post("/api/pages")  # now 2 pages
    resp = client.delete("/api/pages/2")
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}
    assert client.get("/api/pages/2").status_code == 404
