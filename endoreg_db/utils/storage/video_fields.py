"""Guard canonical video bytes before Django storage writes or deletions."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.core.files import File
from django.db.models.fields.files import FieldFile

if TYPE_CHECKING:
    DjangoFile: TypeAlias = File[bytes]
else:
    DjangoFile: TypeAlias = File


class _BinaryFieldFileSaver(Protocol):
    """Narrow Django stubs' unparameterized File at the binary storage boundary."""

    def save(self, name: str, content: DjangoFile, save: bool = True) -> None: ...


@contextmanager
def video_field_mutation(field_file: FieldFile) -> Generator[None]:
    """Generic storage callers guard persisted video owners; other models retain their policy."""
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.media_operation_gate import video_artifact_mutation

    instance: object | None = getattr(field_file, "instance", None)
    if not isinstance(instance, VideoFile):
        yield
        return
    # The model's persisted-row annotation omits Django's unsaved None value.
    video_id = cast(int | None, instance.pk)
    if video_id is None:
        # New instances have no published video generation for a transcode to claim.
        yield
        return
    with video_artifact_mutation(video_id=video_id):
        yield


class VideoArtifactFieldFile(FieldFile):
    """Keep legacy direct FieldFile.save/delete behind the same durable writer lease."""

    def save(self, name: str, content: DjangoFile, save: bool = True) -> None:
        with video_field_mutation(self):
            cast(_BinaryFieldFileSaver, super()).save(name, content, save=save)

    def delete(self, save: bool = True) -> None:
        with video_field_mutation(self):
            super().delete(save=save)
