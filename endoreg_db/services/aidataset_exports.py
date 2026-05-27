from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Sequence

from django.db.models import QuerySet
from lx_dtypes.models.ledger.p_video.Pydantic import PatientVideoFile
from pydantic import BaseModel, Field

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


class AIDataSetFrameLabelExport(BaseModel):
    id: int
    name: str
    labelset_name: str | None = None


class AIDataSetFrameAnnotationExport(BaseModel):
    annotation_id: int
    frame_id: int
    frame_number: int
    timestamp: float | None = None
    relative_path: str
    file_path: str | None = None
    patient_video_file_uuid: str
    video_id: int
    video_uuid: str
    video_hash: str
    original_file_name: str | None = None
    label: AIDataSetFrameLabelExport
    value: bool
    confidence: float | None = None
    annotator: str | None = None
    information_source_name: str | None = None
    model_meta_id: int | None = None
    external_annotation_id: str | None = None
    date_created: datetime
    date_modified: datetime


class AIDataSetExportSummary(BaseModel):
    image_annotation_count: int = 0
    video_annotation_count: int = 0
    frame_count: int = 0
    video_count: int = 0
    label_count: int = 0


class AIDataSetExportPayload(BaseModel):
    schema_version: str = "1.0"
    dataset_id: int
    name: str | None = None
    description: str | None = None
    dataset_type: str
    ai_model_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    summary: AIDataSetExportSummary
    patient_videos: dict[str, PatientVideoFile] = Field(default_factory=dict)
    frame_annotations: list[AIDataSetFrameAnnotationExport] = Field(
        default_factory=list
    )


AIDataSetExportPayload.model_rebuild()


def _resolve_labelset_name(label: Label | None) -> str | None:
    if label is None:
        return None
    labelset = label.label_sets.order_by("-version", "name").first()
    if labelset is None:
        return None
    return labelset.name


def build_frame_annotation_export(
    annotation: ImageClassificationAnnotation,
) -> AIDataSetFrameAnnotationExport:
    frame = annotation.frame
    video = frame.video

    file_path: str | None = None
    try:
        file_path = str(frame.file_path)
    except Exception:
        file_path = None

    return AIDataSetFrameAnnotationExport.model_validate(
        {
            "annotation_id": annotation.pk,
            "frame_id": frame.pk,
            "frame_number": frame.frame_number,
            "timestamp": frame.timestamp,
            "relative_path": frame.relative_path,
            "file_path": file_path,
            "patient_video_file_uuid": str(video.uuid),
            "video_id": video.pk,
            "video_uuid": str(video.uuid),
            "video_hash": video.video_hash,
            "original_file_name": video.original_file_name,
            "label": {
                "id": annotation.label_id,
                "name": annotation.label.name,
                "labelset_name": _resolve_labelset_name(annotation.label),
            },
            "value": annotation.value,
            "confidence": annotation.float_value,
            "annotator": annotation.annotator,
            "information_source_name": (
                annotation.information_source.name
                if annotation.information_source is not None
                else None
            ),
            "model_meta_id": annotation.model_meta_id,
            "external_annotation_id": annotation.external_annotation_id,
            "date_created": annotation.date_created,
            "date_modified": annotation.date_modified,
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
        segment_lists_by_video_id.setdefault(segment.video_file_id, []).append(segment)

    if videos is None:
        videos = dataset.get_related_videos_queryset()

    for video in videos.select_related("sensitive_meta", "state"):
        patient_video = build_lx_patient_video_file(video, include_segments=False)
        attached_segments = segment_lists_by_video_id.get(video.pk, [])
        if attached_segments:
            for segment in attached_segments:
                lx_segment = build_lx_p_video_segment(segment)
                patient_video.patient_video_segments[str(lx_segment.uuid)] = lx_segment
        patient_videos[str(video.uuid)] = patient_video

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
    related_video_ids = {
        annotation.frame.video_id
        for annotation in image_annotations
        if annotation.frame_id is not None and annotation.frame.video_id is not None
    }
    related_video_ids.update(
        segment.video_file_id
        for segment in video_annotations
        if segment.video_file_id is not None
    )
    related_videos = dataset.get_related_videos_queryset().filter(
        pk__in=related_video_ids
    )
    patient_videos = build_patient_videos_export(
        dataset,
        video_annotations=video_annotations,
        videos=related_videos,
    )

    label_ids = {
        annotation.label_id
        for annotation in image_annotations
        if annotation.label_id is not None
    }
    label_ids.update(
        segment.label_id
        for segment in video_annotations
        if segment.label_id is not None
    )

    summary = AIDataSetExportSummary.model_validate(
        {
            "image_annotation_count": len(frame_exports),
            "video_annotation_count": len(video_annotations),
            "frame_count": len(
                {annotation.frame_id for annotation in image_annotations}
            ),
            "video_count": len(patient_videos),
            "label_count": len(label_ids),
        }
    )

    return AIDataSetExportPayload.model_validate(
        {
            "dataset_id": dataset.pk,
            "name": dataset.name,
            "description": dataset.description,
            "dataset_type": dataset.dataset_type,
            "ai_model_type": dataset.ai_model_type,
            "is_active": dataset.is_active,
            "created_at": dataset.created_at,
            "updated_at": dataset.updated_at,
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
) -> dict[str, Any]:
    """
    Return a validated JSON-serializable export payload.
    """
    return build_export_payload(
        dataset,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    ).model_dump(mode="json")
