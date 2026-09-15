import os
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import crud, schemas, storage
from .database import Base, SessionLocal, engine, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        crud.ensure_first_page(db)
    finally:
        db.close()
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
    """Tracks which WebSocket connections are watching which page, so a
    saved stroke can be pushed live to everyone else looking at that page
    — this is what makes drawing feel shared/real-time instead of
    "refresh to see updates"."""

    def __init__(self) -> None:
        self.rooms: Dict[int, List[WebSocket]] = {}

    async def connect(self, page_number: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms.setdefault(page_number, []).append(websocket)

    def disconnect(self, page_number: int, websocket: WebSocket) -> None:
        peers = self.rooms.get(page_number)
        if peers and websocket in peers:
            peers.remove(websocket)
            if not peers:
                del self.rooms[page_number]

    async def broadcast(self, page_number: int, message: dict) -> None:
        for websocket in list(self.rooms.get(page_number, [])):
            try:
                await websocket.send_json(message)
            except Exception:
                # a dead socket will be cleaned up by its own disconnect handler
                pass


manager = ConnectionManager()


@app.get("/api/meta", response_model=schemas.MetaOut)
def read_meta(db: Session = Depends(get_db)):
    return {"count": crud.get_count(db)}


@app.get("/api/pages/{n}", response_model=schemas.PageOut)
def read_page(n: int, db: Session = Depends(get_db)):
    page = crud.get_page(db, n)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return {"page_number": page.page_number, "strokes": page.strokes or []}


@app.post("/api/pages", response_model=schemas.NewPageOut)
def create_page(db: Session = Depends(get_db)):
    page = crud.add_page(db)
    return {"page_number": page.page_number, "count": crud.get_count(db)}


@app.put("/api/pages/{n}", response_model=schemas.PageOut)
async def write_page(n: int, body: schemas.StrokesIn, db: Session = Depends(get_db)):
    strokes = [s.model_dump() for s in body.strokes]
    page = crud.save_strokes(db, n, strokes)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    await manager.broadcast(n, {"type": "strokes", "strokes": strokes})
    return {"page_number": page.page_number, "strokes": page.strokes}


@app.delete("/api/pages/{n}", response_model=schemas.DeleteOut)
def remove_page(n: int, db: Session = Depends(get_db)):
    ok, reason = crud.delete_page(db, n)
    if not ok:
        status_code = 400 if reason == "last_page" else 404
        raise HTTPException(status_code=status_code, detail=reason)
    return {"count": crud.get_count(db)}


@app.get("/api/records", response_model=List[schemas.RecordOut])
def list_records(cat: Optional[str] = None, db: Session = Depends(get_db)):
    return crud.list_records(db, cat)


@app.post("/api/records", response_model=schemas.RecordOut)
def create_record(body: schemas.RecordIn, db: Session = Depends(get_db)):
    return crud.create_record(db, body.model_dump())


@app.put("/api/records/{record_id}", response_model=schemas.RecordOut)
def update_record(record_id: int, body: schemas.RecordIn, db: Session = Depends(get_db)):
    record = crud.update_record(db, record_id, body.model_dump())
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    return record


@app.delete("/api/records/{record_id}")
def remove_record(record_id: int, db: Session = Depends(get_db)):
    ok = crud.delete_record(db, record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="record not found")
    return {"ok": True}


@app.post("/api/uploads", response_model=schemas.UploadOut)
async def upload_photo(file: UploadFile = File(...)):
    url = await storage.upload_photo(file)
    return {"url": url}


@app.websocket("/ws/pages/{n}")
async def page_socket(websocket: WebSocket, n: int):
    await manager.connect(n, websocket)
    try:
        while True:
            # clients don't need to send anything; this just blocks until
            # they disconnect (or send a ping, which we ignore)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(n, websocket)
