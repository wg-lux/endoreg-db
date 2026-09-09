# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

from pathlib import Path
from endoreg_db.export.frames.export_frames_with_labels import (
    DEFAULT_TRANSCODE_FPS,
    _frame_pk_filename,
    transcode_videos_for_annotations,
)
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)


def materialize_frames_for_annotation_ids(
    *,
    annotation_ids: list[int],
    output_root: Path | str,
    fps: float | None = DEFAULT_TRANSCODE_FPS,
    ext: str = "jpg",
    overwrite: bool = False,
) -> dict[int, Path]:
    """
    Materialize frame images needed for model training.

    Uses endoreg-db's existing video/frame extraction logic:
    - ImageClassificationAnnotation -> Frame -> VideoFile
    - VideoFile processed media resolution
    - Frame.frame_number mapping
    - saved as frame_<frame.pk>.<ext>
    ImageClassifa

    Returns:
        mapping: annotation_id -> generated frame path
    """

    output_root = Path(output_root)

    annotations = (
        ImageClassificationAnnotation.objects.select_related(
            "frame",
            "frame__video",
            "label",
        )
        .filter(pk__in=annotation_ids)
        .order_by("frame__video_id", "frame__frame_number", "id")
    )

    if not annotations.exists():
        return {}

    resolved_fps = DEFAULT_TRANSCODE_FPS if fps is None else fps
    transcode_videos_for_annotations(
        annotations,
        fps=resolved_fps,
        quality=2,
        ext=ext,
        overwrite=overwrite,
        export_frame_root=output_root,
    )

    result: dict[int, Path] = {}

    for annotation in annotations:
        frame = annotation.frame
        video = frame.video

        expected_path = (
            output_root / f"video_{video.pk}" / _frame_pk_filename(frame.pk, ext)
        )

        if not expected_path.exists():
            raise FileNotFoundError(
                f"Expected materialized frame missing for "
                f"annotation={annotation.pk}, frame={frame.pk}: {expected_path}"
            )

        if expected_path.stat().st_size <= 0:
            raise RuntimeError(f"Materialized frame is empty: {expected_path}")

        result[annotation.pk] = expected_path

    return result
