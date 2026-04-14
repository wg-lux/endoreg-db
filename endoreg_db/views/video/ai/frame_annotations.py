import logging
import random
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import (
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    ModelMeta,
)
from endoreg_db.serializers.label_video_segment.frame_annotation_bulk import (
    FrameAnnotationBulkItemSerializer,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)
SUPPORTED_FRAME_TASK_MODES = {"random", "filtered"}
DEFAULT_FRAME_INFORMATION_SOURCE_NAME = "manual_annotation"

PREDICTION_INFORMATION_SOURCE_NAMES = {
    "prediction",
    "default_prediction",
    "prediction_annotation",
}


def _build_bulk_upsert_response(
    annotation_items: list[dict[str, Any]],
    requested_video_id: int | None,
    fallback_annotator: str,
) -> Response:
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
    if requested_video_id is not None:
        response_data["video_id"] = requested_video_id

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


def _resolve_request_annotator(request, requested_annotator: str | None = None) -> str:
    if requested_annotator is not None and str(requested_annotator).strip():
        return str(requested_annotator).strip()
    if request.user and request.user.is_authenticated:
        return str(request.user.username)
    return ""


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


def _build_frame_task_queryset(
    *,
    video_id: int | None,
    filter_label_id: int | None,
    information_source_name: str,
    annotator: str,
    exclude_annotated: bool,
    target_label_id: int | None,
    exclude_frame_ids: set[int] | None = None,
):
    frames_qs = Frame.objects.select_related("video")
    if video_id is not None:
        frames_qs = frames_qs.filter(video_id=video_id)

    if filter_label_id is not None:
        frames_qs = frames_qs.filter(
            image_classification_annotations__label_id=filter_label_id,
            image_classification_annotations__value=True,
        )

    if exclude_annotated:
        annotation_filter: dict[str, Any] = {
            "image_classification_annotations__information_source__name": information_source_name
        }
        if annotator:
            annotation_filter["image_classification_annotations__annotator"] = annotator
        if target_label_id is not None:
            annotation_filter["image_classification_annotations__label_id"] = (
                target_label_id
            )
        frames_qs = frames_qs.exclude(**annotation_filter)

    if exclude_frame_ids:
        frames_qs = frames_qs.exclude(id__in=exclude_frame_ids)

    return frames_qs.order_by("id").distinct()


def _pick_random_frame(
    *,
    video_id: int | None,
    filter_label_id: int | None,
    information_source_name: str,
    annotator: str,
    exclude_annotated: bool,
    target_label_id: int | None,
    exclude_frame_ids: set[int] | None = None,
) -> Frame | None:
    frames_qs = _build_frame_task_queryset(
        video_id=video_id,
        filter_label_id=filter_label_id,
        information_source_name=information_source_name,
        annotator=annotator,
        exclude_annotated=exclude_annotated,
        target_label_id=target_label_id,
        exclude_frame_ids=exclude_frame_ids,
    )
    count = frames_qs.count()
    if count == 0:
        return None
    offset = random.randint(0, count - 1)
    return frames_qs[offset]


def _serialize_annotation(annotation: ImageClassificationAnnotation) -> dict[str, Any]:
    return {
        "id": annotation.id,
        "label_id": annotation.label_id,
        "label_name": annotation.label.name,
        "value": annotation.value,
        "float_value": annotation.float_value,
        "annotator": annotation.annotator,
        "information_source_name": (
            annotation.information_source.name
            if annotation.information_source
            else None
        ),
        "model_meta_id": annotation.model_meta_id,
        "external_annotation_id": annotation.external_annotation_id,
    }


def _get_frame_manual_annotations(
    *,
    frame: Frame,
    label_set: LabelSet | None,
    information_source_name: str,
    annotator: str,
):
    queryset = frame.image_classification_annotations.select_related(
        "label", "information_source", "model_meta"
    ).filter(
        information_source__name=information_source_name,
    )
    if label_set is not None:
        queryset = queryset.filter(label__label_sets=label_set)
    if annotator:
        queryset = queryset.filter(annotator=annotator)
    return queryset.order_by("label__name", "id").distinct()


def _get_frame_prediction_annotations(*, frame: Frame, label_set: LabelSet | None):
    queryset = frame.image_classification_annotations.select_related(
        "label", "information_source", "model_meta"
    ).filter(
        Q(information_source__information_source_types__name="prediction")
        | Q(information_source__name__in=PREDICTION_INFORMATION_SOURCE_NAMES)
        | Q(model_meta_id__isnull=False)
    )
    if label_set is not None:
        queryset = queryset.filter(label__label_sets=label_set)
    return queryset.order_by("label__name", "id").distinct()


def _serialize_frame_task(
    frame: Frame,
    *,
    label_set: LabelSet | None,
    target_label: Label | None,
    information_source_name: str,
    annotator: str,
) -> dict[str, Any]:
    label_options = []
    if label_set is not None:
        label_options = [
            {"id": label.id, "name": label.name}
            for label in label_set.labels.all().order_by("name", "id")
        ]
    elif target_label is not None:
        label_options = [{"id": target_label.id, "name": target_label.name}]

    manual_annotations = list(
        _get_frame_manual_annotations(
            frame=frame,
            label_set=label_set,
            information_source_name=information_source_name,
            annotator=annotator,
        )
    )
    prediction_annotations = list(
        _get_frame_prediction_annotations(frame=frame, label_set=label_set)
    )

    manual_positive_ids = [
        annotation.label_id for annotation in manual_annotations if annotation.value
    ]
    prediction_positive_ids = [
        annotation.label_id for annotation in prediction_annotations if annotation.value
    ]

    return {
        "frame_id": frame.id,
        "video_id": frame.video_id,
        "frame_number": frame.frame_number,
        "relative_path": frame.relative_path,
        "frame_stream_path": (
            f"/api/media/videos/{frame.video_id}/frames/{frame.frame_number}/stream/"
        ),
        "annotation_mode": "multilabel",
        "label_options": label_options,
        "manual_annotations": [
            _serialize_annotation(annotation) for annotation in manual_annotations
        ],
        "prediction_annotations": [
            _serialize_annotation(annotation) for annotation in prediction_annotations
        ],
        "manual_positive_label_ids": manual_positive_ids,
        "prediction_positive_label_ids": prediction_positive_ids,
        "suggested_label_ids": manual_positive_ids or prediction_positive_ids,
    }


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

        task_mode = (
            str(request.query_params.get("task_mode", "random") or "random")
            .strip()
            .lower()
        )
        if task_mode not in SUPPORTED_FRAME_TASK_MODES:
            return Response(
                {
                    "error": "task_mode must be one of ['random', 'filtered'].",
                    "details": {"task_mode": task_mode},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        if task_mode == "filtered" and filter_label is None:
            return Response(
                {
                    "error": "filter_label (or previous_label) is required when task_mode='filtered'."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        information_source_name = str(
            request.query_params.get(
                "information_source_name",
                DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
            )
            or DEFAULT_FRAME_INFORMATION_SOURCE_NAME
        ).strip()
        if not information_source_name:
            information_source_name = DEFAULT_FRAME_INFORMATION_SOURCE_NAME

        requested_annotator = request.query_params.get("annotator")
        annotator = _resolve_request_annotator(request, requested_annotator)
        exclude_annotated = _as_bool(
            request.query_params.get("exclude_annotated"), default=True
        )

        tasks: list[dict[str, Any]] = []
        excluded_ids: set[int] = set()
        for _ in range(limit):
            frame = _pick_random_frame(
                video_id=video_id,
                filter_label_id=filter_label.id if filter_label is not None else None,
                information_source_name=information_source_name,
                annotator=annotator,
                exclude_annotated=exclude_annotated,
                target_label_id=target_label.id if target_label is not None else None,
                exclude_frame_ids=excluded_ids,
            )
            if frame is None:
                break
            tasks.append(
                _serialize_frame_task(
                    frame,
                    label_set=label_set,
                    target_label=target_label,
                    information_source_name=information_source_name,
                    annotator=annotator,
                )
            )
            excluded_ids.add(frame.id)

        if not tasks:
            details: dict[str, Any] = {
                "video_id": video_id,
                "information_source_name": information_source_name,
                "annotator": annotator,
                "exclude_annotated": exclude_annotated,
                "task_mode": task_mode,
                "limit": limit,
            }
            if label_set is not None:
                details["label_group_id"] = label_set.id
            if target_label is not None:
                details["target_label"] = target_label.name
            if filter_label is not None:
                details["filter_label"] = filter_label.name
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
            "task_mode": task_mode,
        }
        if label_set is not None:
            response_data["label_group_id"] = label_set.id
        if target_label is not None:
            response_data["target_label"] = target_label.name
        if filter_label is not None:
            response_data["filter_label"] = filter_label.name

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
        annotator = _resolve_request_annotator(request, requested_annotator)
        reason = str(payload.get("reason", "") or "").strip()

        information_source_name = str(
            payload.get(
                "information_source_name",
                DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
            )
            or DEFAULT_FRAME_INFORMATION_SOURCE_NAME
        ).strip()
        if not information_source_name:
            information_source_name = DEFAULT_FRAME_INFORMATION_SOURCE_NAME

        exclude_annotated = _as_bool(payload.get("exclude_annotated"), default=True)
        next_frame = _pick_random_frame(
            video_id=video_id if video_id is not None else frame.video_id,
            filter_label_id=None,
            information_source_name=information_source_name,
            annotator=annotator,
            exclude_annotated=exclude_annotated,
            exclude_frame_ids={frame.id},
            target_label_id=None,
        )

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
        if next_frame is not None:
            response_data["next_task"] = _serialize_frame_task(
                next_frame,
                label_set=None,
                target_label=None,
                information_source_name=information_source_name,
                annotator=annotator,
            )

        return Response(response_data, status=status.HTTP_200_OK)
