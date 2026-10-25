import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Tuple

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import crud, google_auth, models, schemas, search_routes, storage
from .database import Base, SessionLocal, engine, get_db
from .migrations import run_startup_migrations
from .ratelimit import check_rate_limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine.dialect.name == "postgresql":
        # Alembic owns the Postgres schema end to end (see migrations.py) —
        # including creating tables on a brand-new database, which used to
        # be create_all()'s job.
        run_startup_migrations(engine)
    else:
        # SQLite (tests, quick local runs) has no migration history to
        # replay — just build the current schema straight from the models.
        Base.metadata.create_all(bind=engine)
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

# 새 기록 창의 외부 검색(책·음악·영화·애니)과 표지 중계 — search_routes.py.
app.include_router(search_routes.router)


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
# scale (one uvicorn process on one server, and rooms are only ever
# shared with a handful of people) — no Redis needed.
#
# 한도는 "한 사람"이 아니라 "공인 IP 하나" 기준이라는 점에 주의 — 학원·회사처럼
# 여러 PC가 공유기(NAT) 뒤에서 공인 IP 하나로 나가면 다 같은 IP로 보임. 처음
# 온 사람은 페이지를 열자마자 방이 만들어지니(프론트 getMyRoom), 예전 한도
# 5개였으면 학원 단톡방에 링크를 올렸을 때 6번째 사람부터 로비가 "서버에 연결할
# 수 없어요"로 떴을 것. 30명이 한두 번씩 만들어도 남게 50으로 둠.
_room_creations: Dict[str, List[datetime]] = {}
ROOM_CREATE_LIMIT = 50
ROOM_CREATE_WINDOW = timedelta(hours=1)


def _check_room_rate_limit(request: Request) -> None:
    check_rate_limit(request, _room_creations, ROOM_CREATE_LIMIT, ROOM_CREATE_WINDOW,
                      "too many rooms created, try again later")


# 피드백도 로그인 없이 누구나 보낼 수 있어서 방 만들기와 같은 방식으로 막음.
# 한도가 공인 IP 기준인 것도 같음(위 방 만들기 설명) — 한 학원에서 30명이
# 한두 개씩 보내도 남게 60으로 둠.
_feedback_posts: Dict[str, List[datetime]] = {}
FEEDBACK_LIMIT = 60
FEEDBACK_WINDOW = timedelta(hours=1)


def _token_matches(given: Optional[str], real: str) -> bool:
    # 문자열을 ==로 비교하면 앞에서부터 틀린 글자를 만나는 순간 끝나서, 응답
    # 시간 차이로 token을 한 글자씩 맞춰볼 여지가 생김 — 항상 같은 시간이
    # 걸리는 비교를 씀.
    return bool(given) and secrets.compare_digest(given.encode(), real.encode())


def get_room(code: str, db: Session = Depends(get_db)) -> models.Room:
    room = crud.get_room_by_code(db, code)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room


def require_owner(room: models.Room = Depends(get_room), x_room_token: Optional[str] = Header(None)) -> models.Room:
    """Visiting a room by its (shareable) code is always read-only.
    Mutating it additionally requires the room's (secret) token, which
    only the owner's browser holds — sent as the X-Room-Token header."""
    if not _token_matches(x_room_token, room.token):
        raise HTTPException(status_code=403, detail="not room owner")
    return room


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/api/rooms", response_model=schemas.RoomOut)
def create_room(request: Request, db: Session = Depends(get_db)):
    _check_room_rate_limit(request)
    room = crud.create_room(db)
    return {"code": room.code, "token": room.token}


@app.post("/api/feedback")
def send_feedback(body: schemas.FeedbackIn, request: Request, db: Session = Depends(get_db)):
    """모든 페이지의 "의견 보내기" — 저장만 하고 돌려주는 건 없음. 읽는 API는
    일부러 안 만듦(운영자가 서버에서 psql로 봄)."""
    check_rate_limit(request, _feedback_posts, FEEDBACK_LIMIT, FEEDBACK_WINDOW,
                      "too many feedback, try again later")
    crud.create_feedback(db, body.model_dump())
    return {"ok": True}


@app.get("/api/rooms", response_model=List[schemas.PublicRoomOut])
def list_public_rooms(db: Session = Depends(get_db)):
    """"방 둘러보기" — 방 주인이 공개로 켠 방들(기록이 있는 방만, 최근 활동순).
    누구나 볼 수 있는 값(코드·이름·테마·카테고리별 기록 수)만 돌려줌."""
    return crud.list_public_rooms(db)


@app.get("/api/rooms/{code}")
def read_room(room: models.Room = Depends(get_room)):
    return {"code": room.code, "theme": room.theme, "name": room.name, "is_public": room.is_public}


@app.put("/api/rooms/{code}/visibility")
def update_visibility(body: schemas.RoomVisibilityIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    crud.set_public(db, room, body.public)
    return {"is_public": room.is_public}


@app.put("/api/rooms/{code}/theme")
def update_theme(body: schemas.RoomThemeIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    crud.set_theme(db, room, body.theme)
    return {"theme": room.theme}


@app.put("/api/rooms/{code}/name")
def update_name(body: schemas.RoomNameIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    crud.set_name(db, room, body.name)
    return {"name": room.name}


@app.post("/api/auth/google", response_model=schemas.GoogleAuthOut)
def google_auth_resolve(body: schemas.GoogleAuthIn, db: Session = Depends(get_db)):
    """"내 방 복구" — 로그인 시스템이 아니라, 이 브라우저의 localStorage가
    지워졌거나 새 기기일 때 구글 계정으로 원래 방의 token을 다시 받아오는
    용도. 이 구글 계정이 이미 어떤 방에 연결돼 있으면 그 방의 code/token을
    그대로 돌려주고(=복구), 아직 아무 방에도 연결 안 돼 있으면 요청에 실려
    온 현재 방(current_code/current_token, 소유자 토큰으로 증명됨)에
    이 계정을 새로 연결함. 어느 쪽도 안 되면(연결된 방도 없고, 넘어온
    현재 방 정보도 없거나 틀림) 404.

    복구할 때 현재 방이 연결된 방과 다르면 current_empty로 현재 방이
    비었는지도 알려줌 — 새 브라우저가 저절로 만든 빈 방이면 프론트가 묻지
    않고 바로 넘어가고, 기록이 있으면 두고 간다는 걸 경고하게."""
    try:
        claims = google_auth.verify_id_token(body.id_token)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid google token")
    sub = claims["sub"]

    current = None
    if body.current_code and body.current_token:
        found = crud.get_room_by_code(db, body.current_code)
        if found is not None and _token_matches(body.current_token, found.token):
            current = found

    linked = crud.get_room_by_google_sub(db, sub)
    if linked is not None:
        current_empty = None
        if current is not None and current.id != linked.id:
            current_empty = crud.is_room_empty(db, current.id)
        return {"code": linked.code, "token": linked.token, "linked_new": False, "current_empty": current_empty}

    if current is not None:
        crud.set_google_sub(db, current, sub, claims.get("email"))
        return {"code": current.code, "token": current.token, "linked_new": True}

    raise HTTPException(status_code=404, detail="no room linked to this google account")


@app.get("/api/rooms/{code}/google", response_model=schemas.GoogleStatusOut)
def read_google_status(room: models.Room = Depends(require_owner)):
    """방 화면에서 "구글 로그인" 버튼 대신 연동된 계정을 보여줄지 정하는 데
    씀 — 이메일은 다른 사람에게 보일 이유가 없는 정보라 소유자만 조회 가능."""
    return {"linked": room.google_sub is not None, "email": room.google_email}


@app.delete("/api/rooms/{code}/google", response_model=schemas.GoogleStatusOut)
def unlink_google(room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    """연동 해제 — 이 방에 연결해둔 구글 계정 정보(sub/이메일)만 지움. 방의
    code/token과 데이터는 그대로고, 해제 뒤엔 그 구글 계정으로 이 방을
    되찾을 수 없음(같은 계정은 다시 연결하면 다시 쓸 수 있음)."""
    crud.clear_google(db, room)
    return {"linked": False, "email": None}


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


@app.get("/api/rooms/{code}/suggestions", response_model=List[schemas.SuggestionOut])
def suggestions(
    cat: Literal["book", "anime", "movie", "music"],
    field: Literal["title", "creator"],
    q: str = "",
    room: models.Room = Depends(get_room),
    db: Session = Depends(get_db),
):
    """기록 추가 창의 자동 완성(제목 칸·만든 사람 칸) — 이 방 + 공개된 방들의
    기록에서 찾음(자세한 규칙은 crud.suggestions). 다른 GET처럼 코드만 있으면 됨 —
    비공개 방의 기록은 그 방 자신의 후보로만 쓰이고 남에게는 안 나감."""
    return crud.suggestions(db, room, cat, field, q[:100])


@app.post("/api/rooms/{code}/records", response_model=schemas.RecordOut)
def create_record(body: schemas.RecordIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    return crud.create_record(db, room.id, body.model_dump())


@app.put("/api/rooms/{code}/records/{record_id}", response_model=schemas.RecordOut)
def update_record(record_id: int, body: schemas.RecordIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    record = crud.update_record(db, room.id, record_id, body.model_dump())
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    return record


@app.put("/api/rooms/{code}/records/{record_id}/feature", response_model=schemas.RecordOut)
def feature_record(record_id: int, body: schemas.RecordFeatureIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    try:
        record = crud.set_featured(db, room.id, record_id, body.featured)
    except crud.FeaturedLimitError:
        raise HTTPException(status_code=400, detail="featured_limit")
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    return record


@app.put("/api/rooms/{code}/records/{record_id}/playing", response_model=schemas.RecordOut)
def play_record(record_id: int, body: schemas.RecordPlayingIn, room: models.Room = Depends(require_owner), db: Session = Depends(get_db)):
    try:
        record = crud.set_playing(db, room.id, record_id, body.playing)
    except crud.NotMusicError:
        raise HTTPException(status_code=400, detail="not_music")
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
    # 올린 파일은 공개 S3에서 누구나 열 수 있어서, 브라우저가 이미지로만 다루는
    # 형식만 받음 — 예전엔 클라이언트가 보낸 Content-Type을 그대로 믿어서
    # text/html이나 (스크립트를 품을 수 있는) SVG도 공개 URL로 올라갈 수 있었음.
    if file.content_type not in storage.ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="unsupported file type")
    body = await file.read()
    if len(body) > storage.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    await file.seek(0)

    # S3 자격증명이 없거나(로컬 개발 환경 등) 버킷에 문제가 생기면
    # boto3가 예외를 던짐 — 그걸 그대로 흘려보내면 처리 안 된 예외가 돼서
    # CORSMiddleware를 거치지 않고 CORS 헤더 없는 500이 나가버림. 브라우저는
    # 그걸 실제 원인(업로드 실패) 대신 "CORS 에러"로 잘못 표시하니, 여기서
    # 붙잡아 CORS 헤더가 정상적으로 붙는 HTTPException으로 바꿔줌.
    try:
        url = await storage.upload_photo(file)
    except Exception:
        raise HTTPException(status_code=502, detail="upload failed")
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
        pass
    finally:
        # 정상 종료가 아닌 예외로 끝나도 목록에서 빠지게 — 안 그러면 죽은 소켓이
        # 남아 저장할 때마다 broadcast가 헛되이 보내려 함.
        manager.disconnect(key, websocket)
