from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Sequence, cast

from django.db.models import QuerySet
from lx_dtypes.models.contracts.aidataset_export import (
    AIDataSetExportPayload,
    AIDataSetExportSummary,
    AIDataSetFrameAnnotationExport,
    AIDataSetFrameLabelExport,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.ledger.p_video.Pydantic import PatientVideoFile

from endoreg_db.services.hub.deployment import local_study_server_mode_enabled

if TYPE_CHECKING:
    from endoreg_db.models.aidataset.aidataset import AIDataSet
    from endoreg_db.models.label.annotation.image_classification import (
        ImageClassificationAnnotation,
    )
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )
    from endoreg_db.models.media.video.video_file import VideoFile

    ImageAnnotationQuerySet = QuerySet[ImageClassificationAnnotation]
    LabelVideoSegmentQuerySet = QuerySet[LabelVideoSegment]
    VideoFileQuerySet = QuerySet[VideoFile]

__all__ = [
    "AIDataSetExportPayload",
    "AIDataSetExportSummary",
    "AIDataSetFrameAnnotationExport",
    "AIDataSetFrameLabelExport",
    "build_export_payload",
    "build_frame_annotation_export",
    "build_patient_videos_export",
    "export_to_standardized_structure",
    "validate_export_scope",
]


def _resolve_labelset_name(label: Label | None) -> str | None:
    if label is None:
        return None
    label_sets = getattr(label, "label_sets")
    labelset = label_sets.order_by("-version", "name").first()
    if labelset is None:
        return None
    return _model_text(labelset, "name")


def _model_value(instance: object, field_name: str) -> object:
    return getattr(instance, field_name)


def _model_text(instance: object, field_name: str) -> str:
    return str(_model_value(instance, field_name) or "")


def _model_optional_text(instance: object, field_name: str) -> str | None:
    value = _model_value(instance, field_name)
    return str(value) if value not in {None, ""} else None


def _model_bool(instance: object, field_name: str) -> bool:
    value = _model_value(instance, field_name)
    if isinstance(value, bool):
        return value
    return bool(value)


def _model_int(instance: object, field_name: str) -> int:
    value = _model_value(instance, field_name)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError(f"{field_name} must be numeric.")


def _model_optional_int(instance: object, field_name: str) -> int | None:
    value = _model_value(instance, field_name)
    if value is None:
        return None
    return _model_int(instance, field_name)


def _model_optional_float(instance: object, field_name: str) -> float | None:
    value = _model_value(instance, field_name)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"{field_name} must be numeric.")


def _model_datetime(instance: object, field_name: str) -> datetime:
    value = _model_value(instance, field_name)
    if isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime.")


def _frame_ids_for_annotations(
    image_annotations: Sequence[ImageClassificationAnnotation],
) -> set[int]:
    frame_ids: set[int] = set()
    for annotation in image_annotations:
        frame_id = _model_optional_int(annotation, "frame_id")
        if frame_id is not None:
            frame_ids.add(frame_id)
    return frame_ids


def build_frame_annotation_export(
    annotation: ImageClassificationAnnotation,
) -> AIDataSetFrameAnnotationExport:
    frame = _model_value(annotation, "frame")
    video = _model_value(frame, "video")
    label = cast("Label", _model_value(annotation, "label"))
    information_source = _model_value(annotation, "information_source")

    file_path: str | None = None
    try:
        file_path = str(_model_value(frame, "file_path"))
    except (AttributeError, OSError, ValueError):
        file_path = None

    return AIDataSetFrameAnnotationExport.model_validate(
        {
            "annotation_id": _model_int(annotation, "pk"),
            "frame_id": _model_int(frame, "pk"),
            "frame_number": _model_int(frame, "frame_number"),
            "timestamp": _model_optional_float(frame, "timestamp"),
            "relative_path": _model_text(frame, "relative_path"),
            "file_path": file_path,
            "patient_video_file_uuid": str(_model_value(video, "uuid")),
            "video_id": _model_int(video, "pk"),
            "video_uuid": str(_model_value(video, "uuid")),
            "video_hash": _model_text(video, "video_hash"),
            "original_file_name": _model_optional_text(video, "original_file_name"),
            "label": {
                "id": _model_int(annotation, "label_id"),
                "name": _model_text(label, "name"),
                "labelset_name": _resolve_labelset_name(label),
            },
            "value": _model_bool(annotation, "value"),
            "confidence": _model_optional_float(annotation, "float_value"),
            "annotator": _model_optional_text(annotation, "annotator"),
            "information_source_name": (
                _model_text(information_source, "name")
                if information_source is not None
                else None
            ),
            "model_meta_id": _model_optional_int(annotation, "model_meta_id"),
            "external_annotation_id": _model_optional_text(
                annotation,
                "external_annotation_id",
            ),
            "date_created": _model_datetime(annotation, "date_created"),
            "date_modified": _model_datetime(annotation, "date_modified"),
        }
    )


def build_patient_videos_export(
    dataset: AIDataSet,
    *,
    video_annotations: Sequence[LabelVideoSegment] | None = None,
    videos: QuerySet[VideoFile] | None = None,
) -> dict[str, PatientVideoFile]:
    from endoreg_db.services.lx_video_contracts import (
        build_lx_p_video_segment,
        build_lx_patient_video_file,
    )

    patient_videos: dict[str, PatientVideoFile] = {}
    segment_lists_by_video_id: dict[int, list[LabelVideoSegment]] = {}

    if video_annotations is None:
        video_annotations = list(
            dataset.video_annotations.select_related(
                "label",
                "source",
                "video_file",
                "prediction_meta__model_meta__labelset",
                "video_file__ai_model_meta__labelset",
            ).order_by(
                "video_file_id",
                "start_frame_number",
                "end_frame_number",
                "pk",
            )
        )

    for segment in video_annotations:
        segment_lists_by_video_id.setdefault(
            _model_int(segment, "video_file_id"),
            [],
        ).append(segment)

    if videos is None:
        videos = dataset.get_related_videos_queryset()

    for video in videos.select_related("sensitive_meta", "state"):
        patient_video = build_lx_patient_video_file(video, include_segments=False)
        attached_segments = segment_lists_by_video_id.get(_model_int(video, "pk"), [])
        if attached_segments:
            for segment in attached_segments:
                lx_segment = build_lx_p_video_segment(segment)
                patient_video.patient_video_segments[str(lx_segment.uuid)] = lx_segment
        patient_videos[str(_model_value(video, "uuid"))] = patient_video

    return patient_videos


def validate_export_scope(
    *,
    center_key: str | None,
    all_centers: bool,
    only_validated: bool,
) -> str:
    normalized_center_key = (center_key or "").strip()
    if normalized_center_key and all_centers:
        raise ValueError("Export scope must use center_key or all_centers, not both")

    if normalized_center_key:
        from endoreg_db.models.administration.center.center import Center

        if not Center.objects.filter(center_key=normalized_center_key).exists():
            raise ValueError(f"Unknown center_key: {normalized_center_key}")

    if local_study_server_mode_enabled():
        if not (bool(normalized_center_key) ^ bool(all_centers)):
            raise ValueError(
                "local_study_server exports require exactly one center scope: "
                "center_key or all_centers"
            )
        if not only_validated:
            raise ValueError("local_study_server exports require only_validated=true")

    return normalized_center_key


def build_export_payload(
    dataset: AIDataSet,
    *,
    center_key: str | None = None,
    all_centers: bool = False,
    only_validated: bool = False,
) -> AIDataSetExportPayload:
    if dataset.pk is None:
        raise ValueError("AIDataSet must be saved before it can be exported.")

    normalized_center_key = validate_export_scope(
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    )
    image_annotations_qs = dataset.image_annotations.select_related(
        "frame__video",
        "label",
        "information_source",
    )
    video_annotations_qs = dataset.video_annotations.select_related(
        "label",
        "source",
        "video_file",
        "prediction_meta__model_meta__labelset",
        "video_file__ai_model_meta__labelset",
    )

    if normalized_center_key and not all_centers:
        image_annotations_qs = image_annotations_qs.filter(
            frame__video__center__center_key=normalized_center_key
        )
        video_annotations_qs = video_annotations_qs.filter(
            video_file__center__center_key=normalized_center_key
        )
    if only_validated:
        image_annotations_qs = image_annotations_qs.filter(
            frame__video__state__anonymization_validated=True
        )
        video_annotations_qs = video_annotations_qs.filter(
            video_file__state__anonymization_validated=True
        )
        if local_study_server_mode_enabled():
            image_annotations_qs = image_annotations_qs.filter(
                frame__video__state__outside_segments_removed=True,
                frame__video__state__ready_for_export=True,
            ).exclude(frame__video__state__processed_file_sha256="")
            video_annotations_qs = video_annotations_qs.filter(
                video_file__state__outside_segments_removed=True,
                video_file__state__ready_for_export=True,
            ).exclude(video_file__state__processed_file_sha256="")

    image_annotations = list(
        image_annotations_qs.order_by(
            "frame__video_id",
            "frame__frame_number",
            "label__name",
            "pk",
        )
    )
    video_annotations = list(
        video_annotations_qs.order_by(
            "video_file_id",
            "start_frame_number",
            "end_frame_number",
            "pk",
        )
    )

    frame_exports = [
        build_frame_annotation_export(annotation) for annotation in image_annotations
    ]
    related_video_ids: set[int] = set()
    for annotation in image_annotations:
        frame_id = _model_optional_int(annotation, "frame_id")
        frame = _model_value(annotation, "frame")
        video_id = _model_optional_int(frame, "video_id")
        if frame_id is not None and video_id is not None:
            related_video_ids.add(video_id)
    for segment in video_annotations:
        video_file_id = _model_optional_int(segment, "video_file_id")
        if video_file_id is not None:
            related_video_ids.add(video_file_id)
    related_videos = dataset.get_related_videos_queryset().filter(
        pk__in=related_video_ids
    )
    patient_videos = build_patient_videos_export(
        dataset,
        video_annotations=video_annotations,
        videos=related_videos,
    )

    label_ids: set[int] = set()
    for annotation in image_annotations:
        label_id = _model_optional_int(annotation, "label_id")
        if label_id is not None:
            label_ids.add(label_id)
    for segment in video_annotations:
        label_id = _model_optional_int(segment, "label_id")
        if label_id is not None:
            label_ids.add(label_id)

    summary = AIDataSetExportSummary.model_validate(
        {
            "image_annotation_count": len(frame_exports),
            "video_annotation_count": len(video_annotations),
            "frame_count": len(_frame_ids_for_annotations(image_annotations)),
            "video_count": len(patient_videos),
            "label_count": len(label_ids),
        }
    )

    return AIDataSetExportPayload.model_validate(
        {
            "dataset_id": _model_int(dataset, "pk"),
            "name": _model_optional_text(dataset, "name"),
            "description": _model_optional_text(dataset, "description"),
            "dataset_type": _model_text(dataset, "dataset_type"),
            "ai_model_type": _model_text(dataset, "ai_model_type"),
            "is_active": _model_bool(dataset, "is_active"),
            "created_at": _model_datetime(dataset, "created_at"),
            "updated_at": _model_datetime(dataset, "updated_at"),
            "summary": summary,
            "patient_videos": patient_videos,
            "frame_annotations": frame_exports,
        }
    )


def export_to_standardized_structure(
    dataset: AIDataSet,
    *,
    center_key: str | None = None,
    all_centers: bool = False,
    only_validated: bool = False,
) -> JsonObject:
    """
    Return a validated JSON-serializable export payload.
    """
    return build_export_payload(
        dataset,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    ).to_json_object()
