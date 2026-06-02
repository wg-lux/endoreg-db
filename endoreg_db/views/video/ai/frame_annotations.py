import logging
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.models.state.frame_annotation import (
    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
    FrameAnnotationQueueSpec,
    SUPPORTED_FRAME_SAMPLING_STRATEGIES,
    SUPPORTED_FRAME_TASK_MODES,
    ai_dataset_requires_raw_frames,
    build_frame_task_queue,
    normalize_frame_sampling_strategy,
    normalize_frame_task_mode,
    resolve_ai_dataset_for_queue,
    resolve_frame_information_source_name,
    resolve_request_annotator,
)
from endoreg_db.services.frame_retention import (
    prune_unused_validated_outside_frames,
)
from endoreg_db.services.video_files import VideoArtifactKind
from endoreg_db.serializers.label_video_segment.frame_annotation_bulk import (
    FrameAnnotationBulkItemSerializer,
)
from endoreg_db.utils.web.media_urls import build_video_frame_decoded_stream_path
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)

FRAME_FILE_TYPE_AUTO = "auto"
SUPPORTED_FRAME_FILE_TYPES = {
    FRAME_FILE_TYPE_AUTO,
    VideoArtifactKind.RAW.value,
    VideoArtifactKind.PROCESSED.value,
}


def _build_bulk_upsert_response(
    annotation_items: list[dict[str, Any]],
    requested_video_id: int | None,
    fallback_annotator: str,
    ai_dataset_id_raw: Any = None,
) -> Response:
    ai_dataset: AIDataSet | None = None
    if ai_dataset_id_raw not in (None, ""):
        try:
            ai_dataset_id = int(ai_dataset_id_raw)
        except (TypeError, ValueError):
            return Response(
                {"error": "ai_dataset_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ai_dataset = AIDataSet.objects.filter(pk=ai_dataset_id).first()
        if ai_dataset is None:
            return Response(
                {
                    "error": "AIDataSet not found.",
                    "details": {"ai_dataset_id": ai_dataset_id},
                },
                status=status.HTTP_404_NOT_FOUND,
            )

    serializer = FrameAnnotationBulkItemSerializer(data=annotation_items, many=True)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid data.", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    validated_items: list[dict[str, Any]] = serializer.validated_data
    if not validated_items:
        return Response(
            {"error": "At least one annotation is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    frame_ids = {item["frame_id"] for item in validated_items}
    label_ids = {item["label_id"] for item in validated_items}
    source_names = {item["information_source_name"] for item in validated_items}
    model_meta_ids = {
        item["model_meta_id"]
        for item in validated_items
        if item.get("model_meta_id") is not None
    }

    frame_rows = Frame.objects.filter(id__in=frame_ids).values("id", "video_id")
    frame_video_by_id = {row["id"]: row["video_id"] for row in frame_rows}
    missing_frame_ids = sorted(frame_ids - set(frame_video_by_id))
    if missing_frame_ids:
        return Response(
            {
                "error": "Unknown frame_id values.",
                "details": {"missing_frame_ids": missing_frame_ids},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if requested_video_id is not None:
        invalid_frame_ids = sorted(
            [
                frame_id
                for frame_id, video_id in frame_video_by_id.items()
                if video_id != requested_video_id
            ]
        )
        if invalid_frame_ids:
            return Response(
                {
                    "error": "Some frame_id values do not belong to video_id.",
                    "details": {
                        "video_id": requested_video_id,
                        "invalid_frame_ids": invalid_frame_ids,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    known_label_ids = set(
        Label.objects.filter(id__in=label_ids).values_list("id", flat=True)
    )
    missing_label_ids = sorted(label_ids - known_label_ids)
    if missing_label_ids:
        return Response(
            {
                "error": "Unknown label_id values.",
                "details": {"missing_label_ids": missing_label_ids},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if model_meta_ids:
        known_model_meta_ids = set(
            ModelMeta.objects.filter(id__in=model_meta_ids).values_list("id", flat=True)
        )
        missing_model_meta_ids = sorted(model_meta_ids - known_model_meta_ids)
        if missing_model_meta_ids:
            return Response(
                {
                    "error": "Unknown model_meta_id values.",
                    "details": {"missing_model_meta_ids": missing_model_meta_ids},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    source_by_name = {
        source.name: source
        for source in InformationSource.objects.filter(name__in=source_names)
    }
    missing_source_names = sorted(source_names - set(source_by_name))
    if missing_source_names:
        return Response(
            {
                "error": "Unknown information_source_name values.",
                "details": {"missing_information_source_names": missing_source_names},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    date_modified = timezone.now()
    annotations_to_upsert: list[ImageClassificationAnnotation] = []
    for item in validated_items:
        annotator = item.get("annotator")
        if annotator is None:
            annotator = fallback_annotator
        annotations_to_upsert.append(
            ImageClassificationAnnotation(
                frame_id=item["frame_id"],
                label_id=item["label_id"],
                value=item.get("value", True),
                float_value=item.get("float_value"),
                information_source_id=source_by_name[
                    item["information_source_name"]
                ].id,
                annotator=annotator or "",
                model_meta_id=item.get("model_meta_id"),
                external_annotation_id=item.get("external_annotation_id"),
                date_modified=date_modified,
            )
        )

    try:
        with transaction.atomic():
            ImageClassificationAnnotation.objects.bulk_create(
                annotations_to_upsert,
                update_conflicts=True,
                unique_fields=[
                    "frame",
                    "label",
                    "information_source",
                    "annotator",
                ],
                update_fields=[
                    "value",
                    "float_value",
                    "model_meta",
                    "external_annotation_id",
                    "date_modified",
                ],
            )
            attached_frame_annotation_ids: list[int] = []
            if ai_dataset is not None:
                annotation_keys = {
                    (
                        annotation.frame_id,
                        annotation.label_id,
                        annotation.information_source_id,
                        annotation.annotator,
                    )
                    for annotation in annotations_to_upsert
                }
                source_ids = {
                    annotation.information_source_id
                    for annotation in annotations_to_upsert
                }
                annotators = {
                    annotation.annotator for annotation in annotations_to_upsert
                }
                persisted_annotations = [
                    annotation
                    for annotation in ImageClassificationAnnotation.objects.filter(
                        frame_id__in=frame_ids,
                        label_id__in=label_ids,
                        information_source_id__in=source_ids,
                        annotator__in=annotators,
                    )
                    if (
                        annotation.frame_id,
                        annotation.label_id,
                        annotation.information_source_id,
                        annotation.annotator,
                    )
                    in annotation_keys
                ]
                ai_dataset.add_frame_annotations(persisted_annotations)
                attached_frame_annotation_ids = [
                    annotation.pk for annotation in persisted_annotations
                ]
    except Exception as exc:
        logger.error("Bulk frame annotation upsert failed: %s", exc, exc_info=True)
        return Response(
            {"error": "Bulk frame annotation upsert failed."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    response_data: dict[str, Any] = {
        "status": "success",
        "upserted_count": len(annotations_to_upsert),
    }
    if ai_dataset is not None:
        response_data["ai_dataset_id"] = ai_dataset.pk
        response_data["attached_frame_annotation_ids"] = sorted(
            attached_frame_annotation_ids
        )
        response_data["dataset_frame_annotation_count"] = (
            ai_dataset.image_annotations.count()
        )
    target_video_id = requested_video_id
    if target_video_id is None and len(set(frame_video_by_id.values())) == 1:
        target_video_id = next(iter(frame_video_by_id.values()))
    if requested_video_id is not None:
        response_data["video_id"] = requested_video_id
    if target_video_id is not None:
        target_video = VideoFile.objects.filter(pk=target_video_id).first()
        if target_video is not None:
            response_data["pruned_unused_frames"] = (
                prune_unused_validated_outside_frames(target_video)
            )

    return Response(response_data, status=status.HTTP_200_OK)


def _as_int(value: Any, field_name: str) -> tuple[int | None, Response | None]:
    if value is None:
        return None, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, Response(
            {"error": f"{field_name} must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_frame_file_type(value: Any) -> tuple[str | None, Response | None]:
    if value is None or value == "":
        return None, None
    normalized = str(value).strip().lower()
    if normalized in SUPPORTED_FRAME_FILE_TYPES:
        return normalized, None
    return None, Response(
        {
            "error": "frame_file_type must be one of ['auto', 'raw', 'processed'].",
            "details": {"frame_file_type": value},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _video_has_artifact(video: VideoFile, artifact_kind: VideoArtifactKind) -> bool:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        processed_file = getattr(video, "processed_file", None)
        return bool(processed_file and getattr(processed_file, "name", None))
    return bool(getattr(video, "has_raw", False))


def _resolve_task_artifact_kind(
    *,
    video: VideoFile | None,
    requested_frame_file_type: str | None,
) -> VideoArtifactKind | None:
    if video is None or requested_frame_file_type is None:
        return None
    if requested_frame_file_type == VideoArtifactKind.RAW.value:
        return (
            VideoArtifactKind.RAW
            if _video_has_artifact(video, VideoArtifactKind.RAW)
            else None
        )
    if requested_frame_file_type == VideoArtifactKind.PROCESSED.value:
        return (
            VideoArtifactKind.PROCESSED
            if _video_has_artifact(video, VideoArtifactKind.PROCESSED)
            else None
        )
    if _video_has_artifact(video, VideoArtifactKind.PROCESSED):
        return VideoArtifactKind.PROCESSED
    if _video_has_artifact(video, VideoArtifactKind.RAW):
        return VideoArtifactKind.RAW
    return None


def _attach_decoded_frame_stream_paths(
    tasks: list[dict[str, Any]],
    *,
    requested_frame_file_type: str | None,
) -> list[dict[str, Any]]:
    if requested_frame_file_type is None:
        return tasks

    video_ids = {
        int(task["video_id"]) for task in tasks if task.get("video_id") is not None
    }
    videos_by_id = VideoFile.objects.in_bulk(video_ids)

    stream_tasks: list[dict[str, Any]] = []
    for task in tasks:
        video_id = task.get("video_id")
        frame_number = task.get("frame_number")
        if video_id is None or frame_number is None:
            continue
        try:
            video_id_int = int(video_id)
            frame_number_int = int(frame_number)
        except (TypeError, ValueError):
            continue

        artifact_kind = _resolve_task_artifact_kind(
            video=videos_by_id.get(video_id_int),
            requested_frame_file_type=requested_frame_file_type,
        )
        if artifact_kind is None:
            continue

        task_with_stream = dict(task)
        task_with_stream["frame_file_type"] = artifact_kind.value
        task_with_stream["decoded_frame_stream_path"] = (
            build_video_frame_decoded_stream_path(
                video_id_int,
                frame_number_int,
                file_type=artifact_kind.value,
            )
        )
        stream_tasks.append(task_with_stream)

    return stream_tasks


def _as_positive_int(
    value: Any, field_name: str, *, default: int
) -> tuple[int, Response | None]:
    if value is None or value == "":
        return default, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default, Response(
            {"error": f"{field_name} must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed < 1:
        return default, Response(
            {"error": f"{field_name} must be >= 1."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return parsed, None


def _resolve_label_set_for_tasks(
    label_group_id_raw: Any,
) -> tuple[LabelSet | None, Response | None]:
    label_group_id, error = _as_int(label_group_id_raw, "label_group_id")
    if error is not None:
        return None, error
    if label_group_id is None:
        return None, None

    label_set = LabelSet.objects.filter(pk=label_group_id).first()
    if label_set is None:
        return None, Response(
            {
                "error": "Unknown label_group_id.",
                "details": {"label_group_id": label_group_id},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return label_set, None


def _resolve_label_for_tasks(
    *,
    label_name_raw: Any,
    field_name: str,
    label_set: LabelSet | None,
) -> tuple[Label | None, Response | None]:
    if label_name_raw is None:
        return None, None

    label_name = str(label_name_raw).strip()
    if not label_name:
        return None, None

    label_qs = Label.objects.all()
    if label_set is not None:
        label_qs = label_qs.filter(label_sets=label_set)

    label = label_qs.filter(name=label_name).first()
    if label is None:
        label = label_qs.filter(name__iexact=label_name).first()
    if label is None:
        details: dict[str, Any] = {field_name: label_name}
        if label_set is not None:
            details["label_group_id"] = label_set.id
        return None, Response(
            {"error": f"Unknown {field_name}.", "details": details},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return label, None


class FrameAnnotationBulkUpsertView(APIView):
    """
    Bulk upsert endpoint for frame-level annotations.

    Accepted payload formats:
    1) List payload:
       [
         {frame_id, label_id, information_source_name, ...},
         ...
       ]
    2) Object payload:
       {
         "video_id": 123,  # optional safety check
         "annotations": [{...}, {...}]
       }
    """

    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, *args, **kwargs):
        payload = request.data

        requested_video_id: int | None = None
        if isinstance(payload, list):
            annotation_items = payload
        elif isinstance(payload, dict):
            annotation_items = payload.get("annotations")
            requested_video_id_raw = payload.get("video_id")
            if requested_video_id_raw is not None:
                try:
                    requested_video_id = int(requested_video_id_raw)
                except (TypeError, ValueError):
                    return Response(
                        {"error": "video_id must be an integer."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            if annotation_items is None:
                return Response(
                    {
                        "error": "Field 'annotations' is required when payload is an object."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            return Response(
                {"error": "Payload must be a list or an object with 'annotations'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fallback_annotator = ""
        if request.user and request.user.is_authenticated:
            fallback_annotator = request.user.username

        return _build_bulk_upsert_response(
            annotation_items=annotation_items,
            requested_video_id=requested_video_id,
            fallback_annotator=fallback_annotator,
            ai_dataset_id_raw=(
                payload.get("ai_dataset_id") if isinstance(payload, dict) else None
            ),
        )


class FrameAnnotationRandomTaskView(APIView):
    """
    Return one random frame task for annotation.
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, *args, **kwargs):
        limit, error = _as_positive_int(
            request.query_params.get("limit"), "limit", default=1
        )
        if error is not None:
            return error

        video_id, error = _as_int(request.query_params.get("video_id"), "video_id")
        if error is not None:
            return error

        label_set, error = _resolve_label_set_for_tasks(
            request.query_params.get("label_group_id")
        )
        if error is not None:
            return error

        task_mode_raw = (
            str(request.query_params.get("task_mode", "random") or "random")
            .strip()
            .lower()
        )
        if task_mode_raw not in SUPPORTED_FRAME_TASK_MODES:
            return Response(
                {
                    "error": "task_mode must be one of ['random', 'filtered'].",
                    "details": {"task_mode": task_mode_raw},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        task_mode = normalize_frame_task_mode(task_mode_raw)

        target_label, error = _resolve_label_for_tasks(
            label_name_raw=request.query_params.get("target_label"),
            field_name="target_label",
            label_set=label_set,
        )
        if error is not None:
            return error

        filter_label_raw = request.query_params.get("filter_label")
        if filter_label_raw is None:
            filter_label_raw = request.query_params.get("previous_label")
        filter_label, error = _resolve_label_for_tasks(
            label_name_raw=filter_label_raw,
            field_name="filter_label",
            label_set=label_set,
        )
        if error is not None:
            return error

        if task_mode.value == "filtered" and filter_label is None:
            return Response(
                {
                    "error": "filter_label (or previous_label) is required when task_mode='filtered'."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        information_source_name = resolve_frame_information_source_name(
            request.query_params.get(
                "information_source_name",
                request.query_params.get(
                    "information_source",
                    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
                ),
            )
        )

        requested_annotator = request.query_params.get("annotator")
        annotator = resolve_request_annotator(request, requested_annotator)
        exclude_annotated = _as_bool(
            request.query_params.get("exclude_annotated"), default=True
        )
        dataset_frame_filter_raw = (
            str(
                request.query_params.get("dataset_frame_filter", "balanced")
                or "balanced"
            )
            .strip()
            .lower()
        )
        if dataset_frame_filter_raw not in SUPPORTED_FRAME_SAMPLING_STRATEGIES:
            return Response(
                {
                    "error": "dataset_frame_filter must be one of ['balanced', 'segments', 'annotations', 'none'].",
                    "details": {"dataset_frame_filter": dataset_frame_filter_raw},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        sampling_strategy = normalize_frame_sampling_strategy(dataset_frame_filter_raw)
        only_prediction_segments = _as_bool(
            request.query_params.get("prediction_segments_only"), default=True
        )
        try:
            ai_dataset = resolve_ai_dataset_for_queue(
                dataset_id_raw=request.query_params.get("ai_dataset_id"),
                dataset_name_raw=request.query_params.get("ai_dataset_name"),
                dataset_type_raw=request.query_params.get("ai_dataset_type"),
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        requested_frame_file_type, error = _parse_frame_file_type(
            request.query_params.get("frame_file_type")
        )
        if error is not None:
            return error
        raw_video_required = ai_dataset_requires_raw_frames(ai_dataset)
        if raw_video_required and requested_frame_file_type is not None:
            requested_frame_file_type = VideoArtifactKind.RAW.value
        stream_backed_tasks = requested_frame_file_type is not None
        queue_spec = FrameAnnotationQueueSpec(
            limit=limit,
            task_mode=task_mode,
            video_id=video_id,
            label_set=label_set,
            target_label=target_label,
            filter_label=filter_label,
            information_source_name=information_source_name,
            annotator=annotator,
            exclude_annotated=exclude_annotated,
            ai_dataset=ai_dataset,
            sampling_strategy=sampling_strategy,
            prediction_segments_only=only_prediction_segments,
            require_extracted_frames=not stream_backed_tasks,
            require_raw_video=requested_frame_file_type == VideoArtifactKind.RAW.value,
            require_processed_video=(
                requested_frame_file_type == VideoArtifactKind.PROCESSED.value
            ),
            require_streamable_video_artifact=(
                requested_frame_file_type == FRAME_FILE_TYPE_AUTO
            ),
        )
        queue_result = build_frame_task_queue(queue_spec)
        tasks = _attach_decoded_frame_stream_paths(
            queue_result.tasks,
            requested_frame_file_type=requested_frame_file_type,
        )

        if not tasks:
            details: dict[str, Any] = {
                "video_id": video_id,
                "information_source_name": information_source_name,
                "annotator": annotator,
                "exclude_annotated": exclude_annotated,
                "task_mode": task_mode.value,
                "limit": limit,
            }
            if requested_frame_file_type is not None:
                details["frame_file_type"] = requested_frame_file_type
            if label_set is not None:
                details["label_group_id"] = label_set.id
            if target_label is not None:
                details["target_label"] = target_label.name
            if filter_label is not None:
                details["filter_label"] = filter_label.name
            if ai_dataset is not None:
                details["ai_dataset_id"] = ai_dataset.id
                details["ai_dataset_name"] = ai_dataset.name
                details["ai_dataset_type"] = ai_dataset.dataset_type
                details["ai_dataset_model_type"] = ai_dataset.ai_model_type
                details["raw_video_required"] = raw_video_required
            return Response(
                {
                    "error": "No frame task available.",
                    "details": details,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        response_data: dict[str, Any] = {
            "status": "success",
            "task": tasks[0],
            "tasks": tasks,
            "count": len(tasks),
            "task_mode": task_mode.value,
            "selection_strategy": queue_result.selection_strategy,
            "dataset_frame_filter": sampling_strategy.value,
            "prediction_segments_only": only_prediction_segments,
        }
        if requested_frame_file_type is not None:
            response_data["frame_file_type"] = requested_frame_file_type
        if label_set is not None:
            response_data["label_group_id"] = label_set.id
        if target_label is not None:
            response_data["target_label"] = target_label.name
        if filter_label is not None:
            response_data["filter_label"] = filter_label.name
        if ai_dataset is not None:
            response_data["ai_dataset_id"] = ai_dataset.id
            response_data["ai_dataset_name"] = ai_dataset.name
            response_data["ai_dataset_type"] = ai_dataset.dataset_type
            response_data["label_distribution"] = queue_result.label_distribution
            response_data["selected_label_counts"] = queue_result.selected_label_counts
            response_data["segment_bucket_counts"] = queue_result.segment_bucket_counts
            response_data["annotation_bucket_counts"] = (
                queue_result.annotation_bucket_counts
            )
            response_data["bucket_counts"] = queue_result.bucket_counts

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )


class FrameAnnotationSkipView(APIView):
    """
    Acknowledge skipped frame tasks without creating annotations.
    """

    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, *args, **kwargs):
        payload = request.data if isinstance(request.data, dict) else {}

        frame_id, error = _as_int(payload.get("frame_id"), "frame_id")
        if error is not None:
            return error
        if frame_id is None:
            return Response(
                {"error": "Field 'frame_id' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        video_id, error = _as_int(payload.get("video_id"), "video_id")
        if error is not None:
            return error

        try:
            frame = Frame.objects.get(pk=frame_id)
        except Frame.DoesNotExist:
            return Response(
                {"error": "Unknown frame_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if video_id is not None and frame.video_id != video_id:
            return Response(
                {
                    "error": "frame_id does not belong to video_id.",
                    "details": {"frame_id": frame_id, "video_id": video_id},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_annotator = payload.get("annotator")
        annotator = resolve_request_annotator(request, requested_annotator)
        reason = str(payload.get("reason", "") or "").strip()

        information_source_name = resolve_frame_information_source_name(
            payload.get(
                "information_source_name",
                payload.get(
                    "information_source", DEFAULT_FRAME_INFORMATION_SOURCE_NAME
                ),
            )
        )

        exclude_annotated = _as_bool(payload.get("exclude_annotated"), default=True)
        queue_spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=video_id if video_id is not None else frame.video_id,
            information_source_name=information_source_name,
            annotator=annotator,
            exclude_annotated=exclude_annotated,
            sampling_strategy=normalize_frame_sampling_strategy("none"),
            exclude_frame_ids=frozenset({frame.id}),
        )
        queue_result = build_frame_task_queue(queue_spec)

        logger.info(
            "Frame annotation skip: frame_id=%s video_id=%s annotator=%s reason=%s",
            frame.id,
            frame.video_id,
            annotator,
            reason,
        )

        response_data: dict[str, Any] = {
            "status": "success",
            "skipped_frame_id": frame.id,
            "video_id": frame.video_id,
            "annotator": annotator,
            "reason": reason,
        }
        if queue_result.tasks:
            response_data["next_task"] = queue_result.tasks[0]
        response_data["pruned_unused_frames"] = prune_unused_validated_outside_frames(
            frame.video
        )

        return Response(response_data, status=status.HTTP_200_OK)
