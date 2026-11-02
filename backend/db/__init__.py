"""PostgreSQL access: one lazily created engine per process, short transactions via `session()`.

with db.session() as s:        # commits on success, rolls back on error
    repo.create_submission(s, ...)
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import config

_engine = None
_factory = None


def engine():
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset(url: str | None = None):
    """Drop the pooled connections (tests; or after a fork) and optionally switch databases."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None
    if url:
        config.DATABASE_URL = url


@contextmanager
def session() -> Session:
    engine()
    s = _factory()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def ping() -> bool:
    try:
        with engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
