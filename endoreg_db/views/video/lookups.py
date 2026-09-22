from __future__ import annotations

from django.http import Http404

from endoreg_db.models.media.video.video_file import VideoFile


def get_video_or_404(pk: int | str | None) -> VideoFile:
    if pk is None:
        raise Http404("Video ID is required")
    try:
        video_id = int(pk)
    except (TypeError, ValueError) as exc:
        raise Http404("Invalid video ID format") from exc

    try:
        return VideoFile.objects.get(pk=video_id)
    except VideoFile.DoesNotExist as exc:
        raise Http404(f"Video with ID {pk} not found") from exc
