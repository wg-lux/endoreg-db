from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured

from endoreg_db.management.commands import storage_management as command_module
from endoreg_db.models.media.video.video_file import VideoFile


@dataclass
class _ProcessedField:
    name: str


@dataclass
class _Video:
    processed_file: _ProcessedField
    uuid: object


class _TestCommand(command_module.Command):
    def cleanup_processed_video_file(self, video: VideoFile) -> int:
        return self._cleanup_processed_video_file(video)


@pytest.mark.parametrize(
    "size_error",
    [
        AttributeError("missing size"),
        ImproperlyConfigured("storage is not configured"),
        KeyError("plaintext_size"),
        OSError("storage is unavailable"),
        TypeError("size is not numeric"),
        ValueError("size is invalid"),
    ],
)
def test_cleanup_processed_video_preserves_known_size_failure_fallback(
    monkeypatch: pytest.MonkeyPatch,
    size_error: Exception,
) -> None:
    command = _TestCommand()
    command.dry_run = True
    video = cast(
        VideoFile,
        _Video(processed_file=_ProcessedField(name="processed.mp4"), uuid=uuid4()),
    )

    def fail_size(_field_file: object) -> int:
        raise size_error

    monkeypatch.setattr(command_module, "field_file_size", fail_size)

    assert command.cleanup_processed_video_file(video) == 0


def test_cleanup_processed_video_propagates_unexpected_size_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _TestCommand()
    command.dry_run = True
    video = cast(
        VideoFile,
        _Video(processed_file=_ProcessedField(name="processed.mp4"), uuid=uuid4()),
    )

    def fail_size(_field_file: object) -> int:
        raise RuntimeError("unexpected storage implementation failure")

    monkeypatch.setattr(command_module, "field_file_size", fail_size)

    with pytest.raises(
        RuntimeError,
        match="unexpected storage implementation failure",
    ):
        command.cleanup_processed_video_file(video)
