"""Guard canonical video bytes before Django storage writes or deletions."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import cast

from endoreg_db.helpers.typing import DjangoFile, BinaryFieldFileSaver
from django.db.models.fields.files import FieldFile


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
    """Canonical video storage access, with durable ownership for mutations."""

    def exists(self) -> bool:
        return bool(self.name and self.storage.exists(self.name))

    def local_plaintext_path(self) -> Path | None:
        from endoreg_db.utils.storage_streaming import maybe_local_plaintext_path

        return maybe_local_plaintext_path(self)

    def ensure_local(self) -> AbstractContextManager[Path]:
        from endoreg_db.utils.storage.files import ensure_local_file

        return ensure_local_file(self)

    def save(self, name: str, content: DjangoFile, save: bool = True) -> None:
        with video_field_mutation(self):
            cast(BinaryFieldFileSaver, super()).save(name, content, save=save)

    def delete(self, save: bool = True) -> None:
        with video_field_mutation(self):
            super().delete(save=save)

    def get_hash(self) -> str:
        from endoreg_db.utils.file_operations import get_file_hash

        return get_file_hash(self)
