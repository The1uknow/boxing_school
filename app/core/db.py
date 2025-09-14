from __future__ import annotations
from contextlib import contextmanager
from typing import Generator, Iterator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from app.core.config import settings

# -------- Engine --------
DB_URL = settings.DATABASE_URL
IS_SQLITE = DB_URL.startswith(("sqlite", "sqlite+pysqlite"))

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=True,
    future=True,
)

# Включаем внешние ключи в SQLite
if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

# -------- Session factory --------
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)

# -------- Declarative base --------
Base = declarative_base()

# -------- Context manager (для скриптов/админки Flask) --------
@contextmanager
def db_session() -> Iterator[Session]:
    """
    Используй так:
        with db_session() as db:
            ... db.add(...)

    Коммитим на успехе, откатываем при ошибке.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# -------- Dependency for FastAPI --------
def get_db() -> Generator[Session, None, None]:
    """
    Зависимость FastAPI:
        def handler(..., db: Session = Depends(get_db)):
            ...

    Коммитим по завершении обработчика, откат при исключении.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# -------- Bootstrap --------
def init_db() -> None:
    """Создаём таблицы, если их ещё нет (простая инициализация без Alembic)."""
    Base.metadata.create_all(bind=engine)

__all__ = [
    "engine",
    "SessionLocal",
    "Base",
    "db_session",
    "get_db",
    "init_db",
]