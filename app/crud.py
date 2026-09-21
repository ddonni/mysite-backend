import secrets
from datetime import date
from typing import Optional, Tuple

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


def create_record(db: Session, room_id: int, data: dict) -> models.Record:
    record = models.Record(**data, room_id=room_id, date=date.today().isoformat())
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
    db.commit()
    db.refresh(record)
    return record


def set_featured(db: Session, room_id: int, record_id: int, featured: bool) -> Optional[models.Record]:
    """방 안에서 '이달의 작품'은 한 번에 최대 하나 — 새로 켜면 같은
    방의 나머지 기록은 자동으로 꺼서 로비 액자에 걸릴 후보가 항상
    하나 이하가 되게 함."""
    record = _get_record(db, room_id, record_id)
    if record is None:
        return None
    if featured:
        db.query(models.Record).filter(
            models.Record.room_id == room_id, models.Record.id != record_id
        ).update({models.Record.featured: False}, synchronize_session=False)
    record.featured = featured
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
