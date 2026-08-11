"""Dependency-inverted provider boundary for a remotely placed video master."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol, cast

from django.conf import settings
from django.utils.module_loading import import_string


class RemoteProcessedVideoProvider(Protocol):
    def __call__(self, *, video_id: int) -> AbstractContextManager[Path]: ...


def configured_remote_processed_video_provider() -> RemoteProcessedVideoProvider | None:
    dotted_path = str(
        getattr(settings, "ENDOREG_REMOTE_PROCESSED_VIDEO_PROVIDER", "") or ""
    ).strip()
    if not dotted_path:
        return None
    provider = import_string(dotted_path)
    if not isinstance(provider, Callable):
        raise TypeError("configured remote processed-video provider is not callable")
    return cast(RemoteProcessedVideoProvider, provider)


@contextmanager
def materialize_remote_processed_video(*, video_id: int) -> Generator[Path, None, None]:
    provider = configured_remote_processed_video_provider()
    if provider is None:
        raise FileNotFoundError("no remote processed-video provider is configured")
    with provider(video_id=video_id) as local_path:
        path = Path(local_path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise FileNotFoundError(
                "remote processed-video provider returned an invalid local artifact"
            )
        yield path


__all__ = [
    "RemoteProcessedVideoProvider",
    "configured_remote_processed_video_provider",
    "materialize_remote_processed_video",
]
