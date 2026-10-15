"""Operation-scoped file observation without a persistence dependency."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_observer: ContextVar[Callable[[str, str, Path | None, Path | None], None] | None] = (
    ContextVar("file_inventory_observer", default=None)
)


def observe_file_operation(
    operation: str,
    status: str,
    source: Path | None = None,
    destination: Path | None = None,
) -> None:
    observer = _observer.get()
    if observer is not None:
        observer(operation, status, source, destination)


@contextmanager
def file_inventory_scope(
    observer: Callable[[str, str, Path | None, Path | None], None],
) -> Generator[None]:
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)
