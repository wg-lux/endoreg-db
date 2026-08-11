"""Dependency-inverted provider boundary for a remotely placed processed PDF."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol, cast

from django.conf import settings
from django.utils.module_loading import import_string


class RemoteProcessedReportProvider(Protocol):
    def __call__(self, *, report_id: int) -> AbstractContextManager[Path]: ...


@contextmanager
def materialize_remote_processed_report(
    *, report_id: int
) -> Generator[Path, None, None]:
    dotted_path = str(
        getattr(settings, "ENDOREG_REMOTE_PROCESSED_REPORT_PROVIDER", "") or ""
    ).strip()
    if not dotted_path:
        raise FileNotFoundError("no remote processed-report provider is configured")
    loaded = import_string(dotted_path)
    if not isinstance(loaded, Callable):
        raise TypeError("configured remote processed-report provider is not callable")
    provider = cast(RemoteProcessedReportProvider, loaded)
    with provider(report_id=report_id) as local_path:
        path = Path(local_path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise FileNotFoundError(
                "remote processed-report provider returned an invalid local artifact"
            )
        yield path


__all__ = ["RemoteProcessedReportProvider", "materialize_remote_processed_report"]
