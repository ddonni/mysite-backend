import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import crud, models, schemas, storage
from .database import Base, SessionLocal, engine, get_db
from .migrations import run_startup_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)
    yield


app = FastAPI(title="Sketchbook API", lifespan=lifespan)

# Comma-separated list of origins allowed to call this API, e.g.
# "https://yourname.github.io,http://localhost:5500". Defaults to "*"
# (anyone) so local development just works; lock this down once you know
# the real domain the frontend will be served from.
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    """Tracks which WebSocket connections are watching which (room, page),
    so a saved stroke can be pushed live to everyone else looking at that
    page — this is what makes drawing feel shared/real-time instead of
    "refresh to see updates". Rooms are isolated from each other since
    the key includes room_id."""

    def __init__(self) -> None:
        self.rooms: Dict[Tuple[int, int], List[WebSocket]] = {}

    async def connect(self, key: Tuple[int, int], websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms.setdefault(key, []).append(websocket)

    def disconnect(self, key: Tuple[int, int], websocket: WebSocket) -> None:
        peers = self.rooms.get(key)
        if peers and websocket in peers:
            peers.remove(websocket)
            if not peers:
                del self.rooms[key]

    async def broadcast(self, key: Tuple[int, int], message: dict) -> None:
        for websocket in list(self.rooms.get(key, [])):
            try:
                await websocket.send_json(message)
            except Exception:
                # a dead socket will be cleaned up by its own disconnect handler
                pass


manager = ConnectionManager()

# Anyone can create a room with no login, so this is the one endpoint that's
# open to spam (each room costs a Postgres row and, once used, S3 storage).
# A per-IP fixed-window counter in process memory is enough at this site's
# scale (Render free tier runs a single instance, and rooms are only ever
# shared with a handful of people) — no Redis needed.
_room_creations: Dict[str, List[datetime]] = {}
ROOM_CREATE_LIMIT = 5
ROOM_CREATE_WINDOW = timedelta(hours=1)


def _client_ip(request: Request) -> str:
    # Render terminates TLS and proxies to the container, so
    # request.client.host is Render's internal IP, not the visitor's — the
    # real IP is the first entry Render adds to X-Forwarded-For. Fall back
    # to request.client.host for local/test runs where that header is absent.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_room_rate_limit(request: Request) -> None:
    ip = _client_ip(request)
    now = datetime.now(timezone.utc)
    recent = [t for t in _room_creations.get(ip, []) if now - t < ROOM_CREATE_WINDOW]
    if len(recent) >= ROOM_CREATE_LIMIT:
        raise HTTPException(status_code=429, detail="too many rooms created, try again later")
    recent.append(now)
    _room_creations[ip] = recent


def get_room(code: str, db: Session = Depends(get_db)) -> models.Room:
    room = crud.get_room_by_code(db, code)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room


def require_owner(room: models.Room = Depends(get_room), x_room_token: Optional[str] = Header(None)) -> models.Room:
    """Visiting a room by its (shareable) code is always read-only.
    Mutating it additionally requires the room's (secret) token, which
    only the owner's browser holds — sent as the X-Room-Token header."""
    if not x_room_token or x_room_token != room.token:
        raise HTTPException(status_code=403, detail="not room owner")
    return room


@app.post("/api/rooms", response_model=schemas.RoomOut)
def create_room(request: Request, db: Session = Depends(get_db)):
    _check_room_rate_limit(request)
    room = crud.create_room(db)
    return {"code": room.code, "token": room.token}


@app.get("/api/rooms/{code}")
def read_room(room: models.Room = Depends(get_room)):
    return {"code": room.code}


@app.get("/api/rooms/{code}/meta", response_model=schemas.MetaOut)
def read_meta(room: models.Room = Depends(get_room), db: Session = Depends(get_db)):
    return {"count": crud.get_count(db, room.id)}


@app.get("/api/rooms/{code}/pages/{n}", response_model=schemas.PageOut)
def read_page(n: int, room: models.Room = Depends(get_room), db: Session = Depends(get_db)):
    page = crud.get_page(db, room.id, n)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return {"page_number": page.page_number, "strokes": page.strokes or []}


@app.post("/api/rooms/{code}/pages", response_model=schemas.NewPageOut)
def create_page(room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    page = crud.add_page(db, room.id)
    return {"page_number": page.page_number, "count": crud.get_count(db, room.id)}


@app.put("/api/rooms/{code}/pages/{n}", response_model=schemas.PageOut)
async def write_page(n: int, body: schemas.StrokesIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    strokes = [s.model_dump() for s in body.strokes]
    page = crud.save_strokes(db, room.id, n, strokes)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    await manager.broadcast((room.id, n), {"type": "strokes", "strokes": strokes})
    return {"page_number": page.page_number, "strokes": page.strokes}


@app.delete("/api/rooms/{code}/pages/{n}", response_model=schemas.DeleteOut)
def remove_page(n: int, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    ok, reason = crud.delete_page(db, room.id, n)
    if not ok:
        status_code = 400 if reason == "last_page" else 404
        raise HTTPException(status_code=status_code, detail=reason)
    return {"count": crud.get_count(db, room.id)}


@app.get("/api/rooms/{code}/records", response_model=List[schemas.RecordOut])
def list_records(cat: Optional[str] = None, room: models.Room = Depends(get_room), db: Session = Depends(get_db)):
    return crud.list_records(db, room.id, cat)


@app.post("/api/rooms/{code}/records", response_model=schemas.RecordOut)
def create_record(body: schemas.RecordIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    return crud.create_record(db, room.id, body.model_dump())


@app.put("/api/rooms/{code}/records/{record_id}", response_model=schemas.RecordOut)
def update_record(record_id: int, body: schemas.RecordIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    record = crud.update_record(db, room.id, record_id, body.model_dump())
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    return record


@app.delete("/api/rooms/{code}/records/{record_id}")
def remove_record(record_id: int, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    ok = crud.delete_record(db, room.id, record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="record not found")
    return {"ok": True}


@app.post("/api/rooms/{code}/uploads", response_model=schemas.UploadOut)
async def upload_photo(file: UploadFile = File(...), room: models.Room = Depends(require_owner)):
    url = await storage.upload_photo(file)
    return {"url": url}


@app.websocket("/ws/rooms/{code}/pages/{n}")
async def page_socket(websocket: WebSocket, code: str, n: int):
    db = SessionLocal()
    try:
        room = crud.get_room_by_code(db, code)
    finally:
        db.close()
    if room is None:
        await websocket.close(code=4404)
        return

    key = (room.id, n)
    await manager.connect(key, websocket)
    try:
        while True:
            # clients don't need to send anything; this just blocks until
            # they disconnect (or send a ping, which we ignore)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(key, websocket)
