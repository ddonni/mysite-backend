from datetime import date
from typing import Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models


def get_count(db: Session) -> int:
    return db.query(func.count(models.Page.id)).scalar() or 0


def ensure_first_page(db: Session) -> None:
    """Called once at startup: a brand new database needs page 1 to exist."""
    if get_count(db) == 0:
        db.add(models.Page(page_number=1, strokes=[]))
        db.commit()


def get_page(db: Session, page_number: int) -> Optional[models.Page]:
    return db.query(models.Page).filter(models.Page.page_number == page_number).first()


def add_page(db: Session) -> models.Page:
    new_number = get_count(db) + 1
    page = models.Page(page_number=new_number, strokes=[])
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def save_strokes(db: Session, page_number: int, strokes: list) -> Optional[models.Page]:
    page = get_page(db, page_number)
    if page is None:
        return None
    page.strokes = strokes
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def delete_page(db: Session, page_number: int) -> Tuple[bool, Optional[str]]:
    """Delete a page and shift every later page down one slot, so page
    numbers stay contiguous — like tearing a sheet out of a real notebook.

    The shift is two UPDATEs instead of one. page_number has a UNIQUE
    constraint, and a single "SET page_number = page_number - 1 WHERE
    page_number > n" can transiently collide: page 4 might get renumbered
    to 3 before page 3 has been renumbered to 2, and the database checks
    the unique constraint row-by-row as it writes, not at the end of the
    statement. Moving everything into negative numbers first sidesteps
    that entirely, since negative and positive page_numbers never overlap.
    """
    if get_count(db) <= 1:
        return False, "last_page"

    page = get_page(db, page_number)
    if page is None:
        return False, "not_found"

    db.delete(page)
    db.flush()

    later = models.Page.page_number > page_number
    db.query(models.Page).filter(later).update(
        {models.Page.page_number: -models.Page.page_number}, synchronize_session=False
    )
    db.query(models.Page).filter(models.Page.page_number < 0).update(
        {models.Page.page_number: -models.Page.page_number - 1}, synchronize_session=False
    )
    db.commit()
    return True, None


def list_records(db: Session, cat: Optional[str] = None) -> list:
    q = db.query(models.Record)
    if cat is not None:
        q = q.filter(models.Record.cat == cat)
    return q.order_by(models.Record.date.desc(), models.Record.id.desc()).all()


def create_record(db: Session, data: dict) -> models.Record:
    record = models.Record(**data, date=date.today().isoformat())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_record(db: Session, record_id: int, data: dict) -> Optional[models.Record]:
    record = db.query(models.Record).filter(models.Record.id == record_id).first()
    if record is None:
        return None
    for key, value in data.items():
        setattr(record, key, value)
    db.commit()
    db.refresh(record)
    return record


def delete_record(db: Session, record_id: int) -> bool:
    record = db.query(models.Record).filter(models.Record.id == record_id).first()
    if record is None:
        return False
    db.delete(record)
    db.commit()
    return True
