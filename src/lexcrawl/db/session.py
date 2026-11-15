"""### Session factory and unit-of-work helpers for the persistence layer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """### Build a session factory bound to one engine."""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """### Provide one transactional unit of work that always closes its session."""
    session = factory()
    try:
        with session.begin():
            yield session
    finally:
        session.close()
