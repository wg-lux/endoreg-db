from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger(__name__)


class VideoQuerySet(models.QuerySet):
    def next_after(self, last_id=None):
        """
        Return the next VideoFile instance with a primary key greater than the given last_id.
        """
        if last_id is not None:
            try:
                last_id = int(last_id)
            except (ValueError, TypeError):
                return None
        q = self if last_id is None else self.filter(pk__gt=last_id)
        return q.order_by("pk").first()


def _check_hash_exists(cls: type["VideoFile"], video_hash: str) -> bool:
    """
    Checks if a VideoFile with the given raw video hash already exists.
    """
    return cls.objects.filter(video_hash=video_hash).exists()


def _get_all_videos(cls: type["VideoFile"]) -> models.QuerySet["VideoFile"]:
    """
    Returns a queryset containing all VideoFile records.
    """
    return cast(models.QuerySet["VideoFile"], cls.objects.all())


def _get_video_by_pk(pk: int) -> "VideoFile":
    """
    Retrieve a VideoFile instance by its primary key.
    """
    from .video_file import VideoFile

    return VideoFile.objects.get(pk=pk)


def _get_video_by_content_hash(hash: str) -> "VideoFile":
    from .video_file import VideoFile

    try:
        return VideoFile.objects.get(video_hash=hash)
    except Exception as exc:
        logger.error("Video cant be returned for known hash. %s", exc)
        raise
