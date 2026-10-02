"""Database engine and sessions."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    _engine = create_engine(url or get_settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def SessionLocal() -> Session:  # noqa: N802
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def allow_erasure(db: Session) -> None:
    """Permit redaction/deletion of audit rows for the rest of this transaction only."""
    db.execute(text("SET LOCAL onrecord.allow_erasure = 'on'"))
