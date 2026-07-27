from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from endoreg_db.models.media.video.video_file import VideoFile

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _video_file_model() -> type[VideoFile]:
    from endoreg_db.models.media.video.video_file import VideoFile

    return VideoFile


def video_hash_exists(
    video_hash: str, *, model_cls: type["VideoFile"] | None = None
) -> bool:
    model = model_cls or _video_file_model()
    return (
        bool(video_hash)
        and cast(Any, model.objects).filter(video_hash=video_hash).exists()
    )


def get_video_by_pk(pk: int) -> "VideoFile":
    return _video_file_model().objects.get(pk=pk)


def get_video_by_content_hash(content_hash: str) -> "VideoFile":
    return _video_file_model().objects.get(video_hash=content_hash)
