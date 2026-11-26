"""방명록과 좋아요 — 남의 기록(방)에 글을 남기고 좋아요를 누름(2026-11-17).

로그인 시스템은 없어서 "누가"는 **보내는 사람 자신의 방**으로 정함: 브라우저마다 자기 방
(code + 비밀 token)이 있으니, 요청에 그 둘을 X-Actor-Code / X-Actor-Token 헤더로 실어
보냄(방 주인 확인의 X-Room-Token과 같은 방식). 글쓰기·좋아요는 그 방에 **구글 계정이 연결돼
있어야만** 됨(사용자 결정 — 장난 글을 줄이고, 브라우저를 바꿔도 같은 사람으로 남게).

- 헤더가 없거나 틀리면 401 "login_required", 구글 연결이 없으면 403 "google_required"
  (프론트가 이 둘을 보고 "구글 로그인이 필요해요" 창을 띄움).
- 익명 글: 남에게는 이름·사진을 숨기지만 쓴 방은 저장함 — 본인과 방 주인이 지울 수 있게.
- 지우기: 글쓴이 본인 또는 방 주인(구글 연결과 상관없이 토큰만 맞으면).
- 자기 기록엔 좋아요를 못 누름(400 "own_room").
"""
from datetime import timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import crud, models, schemas
from .database import get_db
from .ratelimit import check_rate_limit

router = APIRouter()

GUESTBOOK_LIST_LIMIT = 100
# 한 IP가 한 시간에 남길 수 있는 방명록 글 수 — 학원에서 여럿이 같은 IP로 써도 남게 넉넉히.
_guestbook_posts: Dict[str, list] = {}
GUESTBOOK_POST_LIMIT = 60
GUESTBOOK_POST_WINDOW = timedelta(hours=1)


def _room(code: str, db: Session) -> models.Room:
    room = crud.get_room_by_code(db, code)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room


def _actor(db: Session, code: Optional[str], token: Optional[str]) -> Optional[models.Room]:
    """헤더로 온 "보내는 사람의 방" — 코드와 토큰이 맞을 때만. 아니면 None."""
    if not code or not token:
        return None
    room = crud.get_room_by_code(db, code.upper())
    if room is None or not crud.token_matches(room, token):
        return None
    return room


def optional_actor(
    x_actor_code: Optional[str] = Header(None),
    x_actor_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Optional[models.Room]:
    return _actor(db, x_actor_code, x_actor_token)


def require_actor(actor: Optional[models.Room] = Depends(optional_actor)) -> models.Room:
    if actor is None:
        raise HTTPException(status_code=401, detail="login_required")
    return actor


def _require_google(actor: models.Room) -> None:
    if actor.google_sub is None:
        raise HTTPException(status_code=403, detail="google_required")


def require_google_actor(actor: models.Room = Depends(require_actor)) -> models.Room:
    if actor.google_sub is None:
        raise HTTPException(status_code=403, detail="google_required")
    return actor


def _entry_out(entry: models.GuestbookEntry, room: models.Room, actor: Optional[models.Room]) -> dict:
    author = entry.author
    mine = actor is not None and actor.id == entry.author_room_id
    return {
        "id": entry.id,
        "message": entry.message,
        "anonymous": entry.anonymous,
        "created_at": entry.created_at,
        # 익명이면 남에게는 누가 썼는지 안 보임(본인에게는 보여 줌 — 내가 쓴 글인 걸 알게).
        "author": None if (entry.anonymous and not mine) or author is None else {
            "code": author.code, "name": author.name, "avatar_url": author.avatar_url,
        },
        "mine": mine,
        # 방 주인이 쓴 글(익명이 아니면) — 방문자 화면에서 "주인" 표시.
        "by_owner": entry.author_room_id == room.id and not entry.anonymous,
        "can_delete": actor is not None and (mine or actor.id == room.id),
    }


# ---- 방명록 ----


@router.get("/api/rooms/{code}/guestbook", response_model=schemas.GuestbookListOut)
def list_guestbook(code: str, actor: Optional[models.Room] = Depends(optional_actor), db: Session = Depends(get_db)):
    """방명록 — 코드만 있으면 누구나 읽음(오래된 것부터, 최근 GUESTBOOK_LIST_LIMIT개)."""
    room = _room(code, db)
    total = db.query(func.count(models.GuestbookEntry.id)).filter(models.GuestbookEntry.room_id == room.id).scalar()
    rows = (
        db.query(models.GuestbookEntry)
        .filter(models.GuestbookEntry.room_id == room.id)
        .order_by(models.GuestbookEntry.id.desc())
        .limit(GUESTBOOK_LIST_LIMIT)
        .all()
    )
    return {"total": total, "entries": [_entry_out(e, room, actor) for e in reversed(rows)]}


@router.post("/api/rooms/{code}/guestbook", response_model=schemas.GuestbookOut)
def post_guestbook(
    code: str,
    body: schemas.GuestbookIn,
    request: Request,
    actor: models.Room = Depends(require_actor),
    db: Session = Depends(get_db),
):
    room = _room(code, db)
    # 방 주인은 자기 방명록에 구글 연동 없이도 씀(답글 — 토큰으로 이미 주인임이 증명됨).
    if actor.id != room.id:
        _require_google(actor)
    check_rate_limit(request, _guestbook_posts, GUESTBOOK_POST_LIMIT, GUESTBOOK_POST_WINDOW,
                     "too many guestbook posts, try again later")
    entry = models.GuestbookEntry(room_id=room.id, author_room_id=actor.id, message=body.message, anonymous=body.anonymous)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_out(entry, room, actor)


@router.delete("/api/rooms/{code}/guestbook/{entry_id}")
def delete_guestbook(code: str, entry_id: int, actor: models.Room = Depends(require_actor), db: Session = Depends(get_db)):
    room = _room(code, db)
    entry = (
        db.query(models.GuestbookEntry)
        .filter(models.GuestbookEntry.id == entry_id, models.GuestbookEntry.room_id == room.id)
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="not_found")
    if actor.id not in (entry.author_room_id, room.id):
        raise HTTPException(status_code=403, detail="not_allowed")
    db.delete(entry)
    db.commit()
    return {"ok": True}


# ---- 카드 공감(기록 하나에 누르는 좋아요) ----


def attach_record_likes(db: Session, records: List[models.Record], actor: Optional[models.Room]) -> None:
    """기록 목록에 받은 공감 수(likes)와 보는 사람이 눌렀는지(liked)를 붙임 — 목록 GET(main.py)이 씀.
    DB 칸이 아니라 응답용으로만 잠깐 붙이는 값(RecordOut이 읽음)."""
    ids = [r.id for r in records]
    counts: Dict[int, int] = {}
    mine = set()
    if ids:
        counts = dict(
            db.query(models.RecordLike.record_id, func.count(models.RecordLike.id))
            .filter(models.RecordLike.record_id.in_(ids))
            .group_by(models.RecordLike.record_id)
            .all()
        )
        if actor is not None:
            mine = {rid for (rid,) in db.query(models.RecordLike.record_id).filter(
                models.RecordLike.record_id.in_(ids), models.RecordLike.liker_room_id == actor.id)}
    for r in records:
        r.likes = counts.get(r.id, 0)
        r.liked = r.id in mine


def _record(db: Session, room: models.Room, record_id: int) -> models.Record:
    record = db.query(models.Record).filter(models.Record.id == record_id, models.Record.room_id == room.id).first()
    # 잡동사니는 남에게 안 보이는 기록 — 있는지조차 알려 주지 않게 없는 것과 똑같이.
    if record is None or record.cat in schemas.PRIVATE_CATS:
        raise HTTPException(status_code=404, detail="record not found")
    return record


def _record_likes(db: Session, record: models.Record, actor: models.Room) -> dict:
    count = db.query(func.count(models.RecordLike.id)).filter(models.RecordLike.record_id == record.id).scalar()
    liked = db.query(models.RecordLike.id).filter(
        models.RecordLike.record_id == record.id, models.RecordLike.liker_room_id == actor.id
    ).first() is not None
    return {"count": count, "liked": liked}


@router.put("/api/rooms/{code}/records/{record_id}/likes", response_model=schemas.LikesOut)
def like_record(code: str, record_id: int, actor: models.Room = Depends(require_google_actor), db: Session = Depends(get_db)):
    room = _room(code, db)
    if room.id == actor.id:
        raise HTTPException(status_code=400, detail="own_room")
    record = _record(db, room, record_id)
    exists = db.query(models.RecordLike.id).filter(
        models.RecordLike.record_id == record.id, models.RecordLike.liker_room_id == actor.id
    ).first()
    if exists is None:
        db.add(models.RecordLike(record_id=record.id, liker_room_id=actor.id))
        db.commit()
    return _record_likes(db, record, actor)


@router.delete("/api/rooms/{code}/records/{record_id}/likes", response_model=schemas.LikesOut)
def unlike_record(code: str, record_id: int, actor: models.Room = Depends(require_actor), db: Session = Depends(get_db)):
    room = _room(code, db)
    record = _record(db, room, record_id)
    db.query(models.RecordLike).filter(
        models.RecordLike.record_id == record.id, models.RecordLike.liker_room_id == actor.id
    ).delete()
    db.commit()
    return _record_likes(db, record, actor)


# ---- 좋아요 ----


def _likes(db: Session, room: models.Room, actor: Optional[models.Room]) -> dict:
    count = db.query(func.count(models.RoomLike.id)).filter(models.RoomLike.room_id == room.id).scalar()
    liked = actor is not None and db.query(models.RoomLike.id).filter(
        models.RoomLike.room_id == room.id, models.RoomLike.liker_room_id == actor.id
    ).first() is not None
    return {"count": count, "liked": liked}


@router.get("/api/rooms/{code}/likes", response_model=schemas.LikesOut)
def read_likes(code: str, actor: Optional[models.Room] = Depends(optional_actor), db: Session = Depends(get_db)):
    return _likes(db, _room(code, db), actor)


@router.put("/api/rooms/{code}/likes", response_model=schemas.LikesOut)
def like(code: str, actor: models.Room = Depends(require_google_actor), db: Session = Depends(get_db)):
    room = _room(code, db)
    if room.id == actor.id:
        raise HTTPException(status_code=400, detail="own_room")
    exists = db.query(models.RoomLike.id).filter(
        models.RoomLike.room_id == room.id, models.RoomLike.liker_room_id == actor.id
    ).first()
    if exists is None:
        db.add(models.RoomLike(room_id=room.id, liker_room_id=actor.id))
        db.commit()
    return _likes(db, room, actor)


@router.delete("/api/rooms/{code}/likes", response_model=schemas.LikesOut)
def unlike(code: str, actor: models.Room = Depends(require_actor), db: Session = Depends(get_db)):
    room = _room(code, db)
    db.query(models.RoomLike).filter(
        models.RoomLike.room_id == room.id, models.RoomLike.liker_room_id == actor.id
    ).delete()
    db.commit()
    return _likes(db, room, actor)
