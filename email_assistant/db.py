from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import ATTACHMENTS_DIR, AUTO_CREATE_SCHEMA, DATA_DIR, DATABASE_URL, LOGS_DIR, SQL_ECHO

DATA_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, echo=SQL_ECHO, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def init_db() -> None:
    # Import models so metadata contains all tables.
    import models  # noqa: F401

    if DATABASE_URL.startswith("sqlite"):
        if AUTO_CREATE_SCHEMA:
            Base.metadata.create_all(bind=engine)
        return
    if AUTO_CREATE_SCHEMA:
        raise RuntimeError(
            "AUTO_CREATE_SCHEMA=true is only supported for SQLite. "
            "Run `alembic upgrade head` before starting the app."
        )


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
