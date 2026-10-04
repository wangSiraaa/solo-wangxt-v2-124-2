"""数据库连接。

DATABASE_URL 指向 PostgreSQL，例如
postgresql+psycopg://lab:lab@localhost:5432/matlaboratory
未设置时使用本地 SQLite 文件以便开发与测试。
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+pysqlite:///./matlaboratory.db"
)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from .models import Base
    Base.metadata.create_all(engine)
