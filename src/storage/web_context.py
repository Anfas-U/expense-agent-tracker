"""Request-local storage and state used by authenticated web sessions."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional


_database: ContextVar[Optional[Any]] = ContextVar("web_database", default=None)
_session_state: ContextVar[Optional[Any]] = ContextVar("web_session_state", default=None)
_categorizer: ContextVar[Optional[Any]] = ContextVar("web_categorizer", default=None)


@contextmanager
def user_scope(database: Any, session_state: Any, categorizer: Any) -> Iterator[None]:
    """Bind all data-bearing agent services to one authenticated user's request."""
    database_token = _database.set(database)
    state_token = _session_state.set(session_state)
    categorizer_token = _categorizer.set(categorizer)
    try:
        yield
    finally:
        _categorizer.reset(categorizer_token)
        _session_state.reset(state_token)
        _database.reset(database_token)


def current_database() -> Optional[Any]:
    return _database.get()


def current_session_state() -> Optional[Any]:
    return _session_state.get()


def current_categorizer() -> Optional[Any]:
    return _categorizer.get()


def web_request_active() -> bool:
    return _database.get() is not None
