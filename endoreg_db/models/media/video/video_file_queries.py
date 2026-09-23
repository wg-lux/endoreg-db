from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from .video_file import VideoFile


class VideoQuerySet(models.QuerySet["VideoFile"]):
    def next_after(self, last_id: int | str | None = None) -> VideoFile | None:
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
