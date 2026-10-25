import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models

# Room codes are short and typed by hand (shared with a friend to visit
# their room), so the alphabet drops characters that are easy to
# mis-type or confuse with each other: 0/O, 1/I/L.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 6


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def create_room(db: Session) -> models.Room:
    """A room's code must be globally unique; collisions are astronomically
    unlikely at this alphabet/length (32**6) but we retry just in case."""
    for _ in range(5):
        code = _generate_code()
        if db.query(models.Room).filter(models.Room.code == code).first() is None:
            break
    else:
        raise RuntimeError("could not generate a unique room code")

    room = models.Room(code=code, token=secrets.token_urlsafe(24))
    db.add(room)
    db.commit()
    db.refresh(room)
    db.add(models.Page(room_id=room.id, page_number=1, strokes=[]))
    db.commit()
    return room


def get_room_by_code(db: Session, code: str) -> Optional[models.Room]:
    return db.query(models.Room).filter(models.Room.code == code.upper()).first()


def set_theme(db: Session, room: models.Room, theme: str) -> models.Room:
    room.theme = theme
    db.commit()
    db.refresh(room)
    return room


def set_name(db: Session, room: models.Room, name: str) -> models.Room:
    room.name = name or None  # 빈 문자열은 "이름 없음"으로 저장
    db.commit()
    db.refresh(room)
    return room


def set_public(db: Session, room: models.Room, public: bool) -> models.Room:
    room.is_public = public
    db.commit()
    db.refresh(room)
    return room


PUBLIC_ROOMS_LIMIT = 60


def list_public_rooms(db: Session) -> List[dict]:
    """"방 둘러보기" 목록 — 공개로 켠 방 중 기록이 하나라도 있는 방만(빈
    방은 구경할 게 없어서 뺌), 기록을 가장 최근에 남기거나 고친 방부터.
    방마다 카테고리별 기록 수를 붙여 줌. 규모가 작아서 방 수만큼 따로 세지
    않고, 기록 개수/최근 시각은 한 번의 GROUP BY로 같이 뽑음."""
    rows = (
        db.query(
            models.Room.id,
            models.Record.cat,
            func.count(models.Record.id),
            func.max(models.Record.updated_at),
        )
        .join(models.Record, models.Record.room_id == models.Room.id)
        .filter(models.Room.is_public.is_(True))
        .group_by(models.Room.id, models.Record.cat)
        .all()
    )
    stats: Dict[int, dict] = {}
    for room_id, cat, n, latest in rows:
        s = stats.setdefault(room_id, {"counts": {}, "latest": None})
        s["counts"][cat] = n
        if latest is not None and (s["latest"] is None or latest > s["latest"]):
            s["latest"] = latest
    if not stats:
        return []

    rooms = db.query(models.Room).filter(models.Room.id.in_(stats.keys())).all()
    # 최근 활동 순 — 시각이 없는 경우(이론상)는 맨 뒤로.
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    def latest_of(room: models.Room) -> datetime:
        t = stats[room.id]["latest"]
        if t is None:
            return oldest
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    rooms.sort(key=latest_of, reverse=True)
    out = []
    for room in rooms[:PUBLIC_ROOMS_LIMIT]:
        counts = {cat: stats[room.id]["counts"].get(cat, 0) for cat in ("book", "anime", "movie", "music")}
        out.append({"code": room.code, "name": room.name, "theme": room.theme, "counts": counts, "total": sum(counts.values())})
    return out


SUGGEST_LIMIT = 8
# 남의 방 기록은 이 수 이상의 (공개) 방에서 쓰인 값만 후보로 — 한 방에만 있는
# 오타나 장난 입력이 다른 사람 자동 완성에 퍼지지 않게.
SUGGEST_MIN_ROOMS = 2


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").split()).lower()


def suggestions(db: Session, room: models.Room, cat: str, field: str, q: str) -> List[dict]:
    """기록 추가 창의 자동 완성(제목 칸·만든 사람 칸 각각) — 이 방의 기록 +
    공개된 방의 기록 중 그 칸(field: "title" | "creator") 값에 q가 들어간 것.
    같은 값(공백·대소문자 무시)은 하나로 묶고, 표기가 갈리면 가장 많이 쓰인 표기로.
    - 남의 방에서만 나온 값은 SUGGEST_MIN_ROOMS개 이상의 방에 있어야 함.
    - mine: 이 방에 이미 있는 값인지(프론트는 제목 칸에서만 "이미 기록함"으로 씀).
    - 정렬: q로 시작하는 값 먼저, 그다음 많은 방에서 쓰인 순.
    두 칸은 서로 독립 — 제목 후보가 만든 사람을 알려주지 않음(동명의 다른
    작품이 흔하고, 만든 사람을 모르고 기록하는 경우도 많아서)."""
    q = _norm(q)
    if not q:
        return []
    column = models.Record.title if field == "title" else models.Record.creator
    rows = (
        db.query(column, models.Record.room_id)
        .join(models.Room, models.Room.id == models.Record.room_id)
        .filter(models.Record.cat == cat)
        .filter(func.lower(column).contains(q))
        .filter((models.Record.room_id == room.id) | (models.Room.is_public.is_(True)))
        .limit(2000)
        .all()
    )
    groups: Dict[str, dict] = {}
    for value, room_id in rows:
        key = _norm(value)
        if not key:
            continue
        g = groups.setdefault(key, {"spellings": {}, "rooms": set()})
        spelling = value.strip()
        g["spellings"][spelling] = g["spellings"].get(spelling, 0) + 1
        g["rooms"].add(room_id)

    out = []
    for key, g in groups.items():
        mine = room.id in g["rooms"]
        if not mine and len(g["rooms"]) < SUGGEST_MIN_ROOMS:
            continue
        value = max(g["spellings"].items(), key=lambda kv: kv[1])[0]
        out.append({"value": value, "mine": mine, "rooms": len(g["rooms"]), "_prefix": key.startswith(q)})
    out.sort(key=lambda s: (not s["_prefix"], -s["rooms"], s["value"]))
    for s in out:
        del s["_prefix"]
    return out[:SUGGEST_LIMIT]


def get_room_by_google_sub(db: Session, sub: str) -> Optional[models.Room]:
    return db.query(models.Room).filter(models.Room.google_sub == sub).first()


def clear_google(db: Session, room: models.Room) -> models.Room:
    room.google_sub = None
    room.google_email = None
    db.commit()
    db.refresh(room)
    return room


def set_google_sub(db: Session, room: models.Room, sub: str, email: Optional[str]) -> models.Room:
    room.google_sub = sub
    room.google_email = email
    db.commit()
    db.refresh(room)
    return room


def is_room_empty(db: Session, room_id: int) -> bool:
    """No records and no strokes on any page — i.e. the room a new browser
    creates on its own before the visitor has done anything in it."""
    if db.query(models.Record.id).filter(models.Record.room_id == room_id).first() is not None:
        return False
    pages = db.query(models.Page.strokes).filter(models.Page.room_id == room_id).all()
    return all(not strokes for (strokes,) in pages)


def get_count(db: Session, room_id: int) -> int:
    return db.query(func.count(models.Page.id)).filter(models.Page.room_id == room_id).scalar() or 0


def get_page(db: Session, room_id: int, page_number: int) -> Optional[models.Page]:
    return (
        db.query(models.Page)
        .filter(models.Page.room_id == room_id, models.Page.page_number == page_number)
        .first()
    )


def add_page(db: Session, room_id: int) -> models.Page:
    new_number = get_count(db, room_id) + 1
    page = models.Page(room_id=room_id, page_number=new_number, strokes=[])
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def save_strokes(db: Session, room_id: int, page_number: int, strokes: list) -> Optional[models.Page]:
    page = get_page(db, room_id, page_number)
    if page is None:
        return None
    page.strokes = strokes
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def delete_page(db: Session, room_id: int, page_number: int) -> Tuple[bool, Optional[str]]:
    """Delete a page and shift every later page (in the same room) down one
    slot, so page numbers stay contiguous — like tearing a sheet out of a
    real notebook.

    The shift is two UPDATEs instead of one. (room_id, page_number) has a
    UNIQUE constraint, and a single "SET page_number = page_number - 1
    WHERE page_number > n" can transiently collide: page 4 might get
    renumbered to 3 before page 3 has been renumbered to 2, and the
    database checks the unique constraint row-by-row as it writes, not at
    the end of the statement. Moving everything into negative numbers
    first sidesteps that entirely, since negative and positive
    page_numbers never overlap.
    """
    if get_count(db, room_id) <= 1:
        return False, "last_page"

    page = get_page(db, room_id, page_number)
    if page is None:
        return False, "not_found"

    db.delete(page)
    db.flush()

    later = (models.Page.room_id == room_id) & (models.Page.page_number > page_number)
    db.query(models.Page).filter(later).update(
        {models.Page.page_number: -models.Page.page_number}, synchronize_session=False
    )
    shifted = (models.Page.room_id == room_id) & (models.Page.page_number < 0)
    db.query(models.Page).filter(shifted).update(
        {models.Page.page_number: -models.Page.page_number - 1}, synchronize_session=False
    )
    db.commit()
    return True, None


def list_records(db: Session, room_id: int, cat: Optional[str] = None) -> list:
    q = db.query(models.Record).filter(models.Record.room_id == room_id)
    if cat is not None:
        q = q.filter(models.Record.cat == cat)
    return q.order_by(models.Record.date.desc(), models.Record.id.desc()).all()


# 기록 날짜는 "사용자가 사는 곳의 오늘" — 예전 서버(Render)는 UTC라 date.today()를
# 쓰면 한국 시간 0~9시에 남긴 기록이 어제 날짜로 찍혔음. 지금 서버는 Asia/Seoul이지만
# 서버 시간대 설정에 기대지 않으려고 그대로 둠. 한국은 서머타임이 없어서 고정
# +9시간이면 충분함(tzdata 패키지 없이도 동작).
KST = timezone(timedelta(hours=9))


def today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def create_record(db: Session, room_id: int, data: dict) -> models.Record:
    record = models.Record(**data, room_id=room_id, date=today_kst())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _get_record(db: Session, room_id: int, record_id: int) -> Optional[models.Record]:
    # update_record/set_featured/delete_record 모두 "이 방 소유의 이 기록"을
    # 찾는 걸로 시작함 — room_id를 필터에 같이 걸어서, URL의 record_id를 알아도
    # 남의 방 기록은 절대 못 건드리게(스코핑) 함.
    return (
        db.query(models.Record)
        .filter(models.Record.id == record_id, models.Record.room_id == room_id)
        .first()
    )


def update_record(db: Session, room_id: int, record_id: int, data: dict) -> Optional[models.Record]:
    record = _get_record(db, room_id, record_id)
    if record is None:
        return None
    for key, value in data.items():
        setattr(record, key, value)
    # 음악이 아닌 카테고리로 고쳐졌으면 턴테이블에서도 내림.
    if record.cat != "music":
        record.playing = False
    db.commit()
    db.refresh(record)
    return record


# 로비에서 카테고리마다 인생작 자리가 3개씩 있음(책장 위 액자 3개, 영화
# 큰 포스터 3장, 애니 진열장 윗칸 스탠드 3개) — 그 자리 수와 맞춤.
FEATURED_PER_CAT = 3


class FeaturedLimitError(Exception):
    """이 카테고리의 인생작이 이미 FEATURED_PER_CAT개 켜져 있음."""


def set_featured(db: Session, room_id: int, record_id: int, featured: bool) -> Optional[models.Record]:
    """인생작은 방 안에서 카테고리마다 최대 FEATURED_PER_CAT개. 이미 꽉 찬
    카테고리에 하나 더 켜려고 하면, 다른 걸 몰래 끄는 대신
    FeaturedLimitError를 던져서 사용자가 직접 하나를 해제하게 함."""
    record = _get_record(db, room_id, record_id)
    if record is None:
        return None
    if featured and not record.featured:
        count = (
            db.query(func.count(models.Record.id))
            .filter(models.Record.room_id == room_id, models.Record.cat == record.cat, models.Record.featured.is_(True))
            .scalar()
        )
        if count >= FEATURED_PER_CAT:
            raise FeaturedLimitError()
    record.featured = featured
    db.commit()
    db.refresh(record)
    return record


class NotMusicError(Exception):
    """음악이 아닌 기록을 턴테이블에 올리려고 함."""


def set_playing(db: Session, room_id: int, record_id: int, playing: bool) -> Optional[models.Record]:
    """로비 턴테이블에서 돌릴 곡을 직접 고름 — 방마다 한 곡뿐이라, 켜면 이
    방에서 켜져 있던 다른 곡은 끔. 끄면 아무 곡도 안 고른 상태가 되고 프론트가
    기본 규칙(최애음악 첫 번째, 없으면 최신 곡)으로 정함."""
    record = _get_record(db, room_id, record_id)
    if record is None:
        return None
    if playing:
        if record.cat != "music":
            raise NotMusicError()
        db.query(models.Record).filter(
            models.Record.room_id == room_id, models.Record.playing.is_(True), models.Record.id != record_id
        ).update({models.Record.playing: False}, synchronize_session=False)
    record.playing = playing
    db.commit()
    db.refresh(record)
    return record


def delete_record(db: Session, room_id: int, record_id: int) -> bool:
    record = _get_record(db, room_id, record_id)
    if record is None:
        return False
    db.delete(record)
    db.commit()
    return True


def create_feedback(db: Session, data: dict) -> models.Feedback:
    data["name"] = data.get("name") or None  # 빈 문자열은 "이름 없음"으로 저장
    feedback = models.Feedback(**data)
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback
