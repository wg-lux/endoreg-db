from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import cast
from endoreg_db.export.frames.export_frames_with_labels import (
    DEFAULT_TRANSCODE_FPS,
    _frame_pk_filename,
    transcode_videos_for_annotations,
)
from endoreg_db.models import ImageClassificationAnnotation, VideoFile


def materialize_frames_for_annotation_ids(
    *,
    annotation_ids: list[int],
    output_root: Path | str,
    fps: float | None = DEFAULT_TRANSCODE_FPS,
    ext: str = "jpg",
    overwrite: bool = False,
    max_frames_per_video: int | None = None,  # DEBUG ONLY
) -> dict[int, Path]:
    output_root = Path(output_root).expanduser().resolve()

    annotations = (
        ImageClassificationAnnotation.objects.select_related(
            "frame",
            "frame__video",
            "label",
        )
        .filter(pk__in=annotation_ids)
        .order_by("frame__video_id", "frame__frame_number", "id")
    )

    ann_count = annotations.count()

    print(f"[ENDOREG FRAME MATERIALIZATION] annotation rows={ann_count}", flush=True)
    print(f"[ENDOREG FRAME MATERIALIZATION] output_root={output_root}", flush=True)
    print(
        f"[ENDOREG FRAME MATERIALIZATION] fps={fps}, ext={ext}, "
        f"overwrite={overwrite}, max_frames_per_video={max_frames_per_video}",
        flush=True,
    )

    if ann_count == 0:
        return {}

    if max_frames_per_video is not None:
        limited_ids: list[int] = []
        per_video_count: dict[int, int] = defaultdict(int)

        for ann in annotations.iterator(chunk_size=2000):
            frame = ann.frame
            if frame is None or frame.video_id is None:
                continue

            video_id = int(frame.video_id)

            if per_video_count[video_id] >= max_frames_per_video:
                continue

            limited_ids.append(int(ann.pk))
            per_video_count[video_id] += 1

        annotations = (
            ImageClassificationAnnotation.objects.select_related(
                "frame",
                "frame__video",
                "label",
            )
            .filter(pk__in=limited_ids)
            .order_by("frame__video_id", "frame__frame_number", "id")
        )

        print(
            "[ENDOREG FRAME MATERIALIZATION] DEBUG LIMIT ACTIVE: "
            f"{len(limited_ids)} annotations selected "
            f"from {len(per_video_count)} videos",
            flush=True,
        )

    video_ids = list(
        annotations.exclude(frame__video_id__isnull=True)
        .values_list("frame__video_id", flat=True)
        .order_by("frame__video_id")
        .distinct()
    )

    print(f"[ENDOREG FRAME MATERIALIZATION] video_count={len(video_ids)}", flush=True)

    for video in VideoFile.objects.filter(pk__in=video_ids).order_by("pk"):
        expected_output_dir = output_root / f"video_{video.pk}"
        print(
            f"  video_id={video.pk} "
            f"uuid={getattr(video, 'uuid', None)} "
            f"processed_file={getattr(video.processed_file, 'name', video.processed_file)} "
            f"output_dir={expected_output_dir}",
            flush=True,
        )

    print(
        "[ENDOREG FRAME MATERIALIZATION] starting endoreg-db transcode logic",
        flush=True,
    )

    transcode_videos_for_annotations(
        annotations,
        fps=cast(float, fps),
        quality=2,
        ext=ext,
        overwrite=overwrite,
        export_frame_root=output_root,
    )

    print("[ENDOREG FRAME MATERIALIZATION] transcode logic finished", flush=True)

    result: dict[int, Path] = {}

    for annotation in annotations.iterator(chunk_size=2000):
        frame = annotation.frame
        if frame is None or frame.video is None:
            continue

        expected_path = (
            output_root / f"video_{frame.video.pk}" / _frame_pk_filename(frame.pk, ext)
        )

        if not expected_path.exists():
            raise FileNotFoundError(
                f"Expected materialized frame missing for "
                f"annotation={annotation.pk}, frame={frame.pk}: {expected_path}"
            )

        if expected_path.stat().st_size <= 0:
            raise RuntimeError(f"Materialized frame is empty: {expected_path}")

        result[int(annotation.pk)] = expected_path

    print(f"[ENDOREG FRAME MATERIALIZATION] verified frames={len(result)}", flush=True)
    return result
