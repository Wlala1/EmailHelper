from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from config import ATTACHMENTS_DIR, DATA_DIR, DATABASE_URL, LOGS_DIR, SQL_ECHO

DATA_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, echo=SQL_ECHO, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def _ensure_additive_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    additive_columns = {
        "users": {
            "last_login_at_utc": "TIMESTAMP",
        },
        "emails": {
            "direction": "VARCHAR(16)",
            "mailbox_folder": "VARCHAR(32)",
            "graph_parent_folder_id": "VARCHAR(255)",
            "mailbox_last_modified_at_utc": "TIMESTAMP",
            "processed_mode": "VARCHAR(32)",
        },
    }

    with engine.begin() as connection:
        for table_name, columns in additive_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def init_db() -> None:
    # Import models so metadata contains all tables.
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_additive_columns()


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
