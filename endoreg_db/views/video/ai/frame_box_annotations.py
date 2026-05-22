import logging
from typing import Any

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import (
    Frame,
    FrameBoxAnnotation,
    InformationSource,
    Label,
    ModelMeta,
)
from endoreg_db.models.state.frame_annotation import (
    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
    resolve_frame_information_source_name,
    resolve_request_annotator,
)
from endoreg_db.serializers.label_video_segment.frame_box_annotation import (
    FrameBoxAnnotationBulkItemSerializer,
    FrameBoxAnnotationSerializer,
)
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


def _as_int(value: Any, field_name: str) -> tuple[int | None, Response | None]:
    if value is None or value == "":
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


def _annotation_scope_filter(
    *,
    frame_id: int,
    information_source_name: str,
    annotator: str,
) -> Q:
    return Q(
        frame_id=frame_id,
        information_source__name=information_source_name,
        annotator=annotator,
    )


class FrameBoxAnnotationView(APIView):
    """
    Persist and list general box-based frame annotations.

    GET requires frame_id. POST accepts either a list of box annotations or an
    object with {frame_id, video_id, replace, annotations}.
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, *args, **kwargs):
        frame_id, error = _as_int(request.query_params.get("frame_id"), "frame_id")
        if error is not None:
            return error
        if frame_id is None:
            return Response(
                {"error": "frame_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            frame = Frame.objects.only("id", "video_id").get(pk=frame_id)
        except Frame.DoesNotExist:
            return Response(
                {"error": "Unknown frame_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        video_id, error = _as_int(request.query_params.get("video_id"), "video_id")
        if error is not None:
            return error
        if video_id is not None and frame.video_id != video_id:
            return Response(
                {
                    "error": "frame_id does not belong to video_id.",
                    "details": {"frame_id": frame_id, "video_id": video_id},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = FrameBoxAnnotation.objects.select_related(
            "label",
            "information_source",
            "model_meta",
        ).filter(frame=frame)

        information_source_name = request.query_params.get("information_source_name")
        if information_source_name is None:
            information_source_name = request.query_params.get("information_source")
        if information_source_name:
            queryset = queryset.filter(
                information_source__name=resolve_frame_information_source_name(
                    information_source_name
                )
            )

        annotator = request.query_params.get("annotator")
        if annotator is not None:
            queryset = queryset.filter(
                annotator=resolve_request_annotator(request, annotator)
            )

        serializer = FrameBoxAnnotationSerializer(
            queryset.order_by("label__name", "id"),
            many=True,
        )
        return Response(
            {
                "status": "success",
                "frame_id": frame.id,
                "video_id": frame.video_id,
                "annotations": serializer.data,
                "count": len(serializer.data),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, *args, **kwargs):
        payload = request.data
        replace = False
        requested_video_id: int | None = None
        frame_id: int | None = None
        payload_annotator: str | None = None
        payload_information_source_name: str | None = None

        if isinstance(payload, list):
            annotation_items = payload
        elif isinstance(payload, dict):
            annotation_items = payload.get("annotations")
            replace = _as_bool(payload.get("replace"), default=False)
            payload_annotator = payload.get("annotator")
            payload_information_source_name = payload.get("information_source_name")
            if payload_information_source_name is None:
                payload_information_source_name = payload.get("information_source")

            frame_id, error = _as_int(payload.get("frame_id"), "frame_id")
            if error is not None:
                return error

            requested_video_id, error = _as_int(payload.get("video_id"), "video_id")
            if error is not None:
                return error

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

        if not isinstance(annotation_items, list):
            return Response(
                {"error": "annotations must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if replace and not annotation_items:
            return self._delete_empty_replace_scope(
                frame_id=frame_id,
                information_source_name=payload_information_source_name,
                annotator=payload_annotator,
                request=request,
            )

        normalized_items: list[dict[str, Any]] = []
        for raw_item in annotation_items:
            if not isinstance(raw_item, dict):
                return Response(
                    {"error": "Each annotation must be an object."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            item = dict(raw_item)
            if frame_id is not None and item.get("frame_id") in {None, ""}:
                item["frame_id"] = frame_id
            if not item.get("information_source_name"):
                item["information_source_name"] = (
                    payload_information_source_name
                    or DEFAULT_FRAME_INFORMATION_SOURCE_NAME
                )
            if item.get("annotator") in {None, ""} and payload_annotator is not None:
                item["annotator"] = payload_annotator
            normalized_items.append(item)

        serializer = FrameBoxAnnotationBulkItemSerializer(
            data=normalized_items,
            many=True,
        )
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid data.", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_items: list[dict[str, Any]] = serializer.validated_data
        if replace and frame_id is None:
            frame_ids = {item["frame_id"] for item in validated_items}
            if len(frame_ids) == 1:
                frame_id = next(iter(frame_ids))
            else:
                return Response(
                    {"error": "frame_id is required when replace=true."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return self._persist_validated_items(
            validated_items=validated_items,
            requested_video_id=requested_video_id,
            replace=replace,
            request=request,
        )

    def _delete_empty_replace_scope(
        self,
        *,
        frame_id: int | None,
        information_source_name: str | None,
        annotator: str | None,
        request,
    ) -> Response:
        if frame_id is None:
            return Response(
                {"error": "frame_id is required when replace=true."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            frame = Frame.objects.only("id", "video_id").get(pk=frame_id)
        except Frame.DoesNotExist:
            return Response(
                {"error": "Unknown frame_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_name = resolve_frame_information_source_name(
            information_source_name or DEFAULT_FRAME_INFORMATION_SOURCE_NAME
        )
        source = InformationSource.objects.filter(name=source_name).first()
        if source is None:
            return Response(
                {
                    "error": "Unknown information_source_name values.",
                    "details": {"missing_information_source_names": [source_name]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolved_annotator = resolve_request_annotator(request, annotator)
        deleted_count, _ = FrameBoxAnnotation.objects.filter(
            frame=frame,
            information_source__name=source.name,
            annotator=resolved_annotator,
        ).delete()
        return Response(
            {
                "status": "success",
                "video_id": frame.video_id,
                "upserted_count": 0,
                "deleted_count": deleted_count,
                "annotations": [],
            },
            status=status.HTTP_200_OK,
        )

    def _persist_validated_items(
        self,
        *,
        validated_items: list[dict[str, Any]],
        requested_video_id: int | None,
        replace: bool,
        request,
    ) -> Response:
        frame_ids = {item["frame_id"] for item in validated_items}
        label_ids = {item["label_id"] for item in validated_items}
        source_names = {
            resolve_frame_information_source_name(item["information_source_name"])
            for item in validated_items
        }
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
                    current_frame_id
                    for current_frame_id, video_id in frame_video_by_id.items()
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
                ModelMeta.objects.filter(id__in=model_meta_ids).values_list(
                    "id",
                    flat=True,
                )
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
                    "details": {
                        "missing_information_source_names": missing_source_names
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        fallback_annotator = ""
        if request.user and request.user.is_authenticated:
            fallback_annotator = request.user.username

        try:
            with transaction.atomic():
                if replace:
                    self._delete_replaced_scope(
                        validated_items=validated_items,
                        source_by_name=source_by_name,
                        fallback_annotator=fallback_annotator,
                    )
                annotations = self._upsert_items(
                    validated_items=validated_items,
                    source_by_name=source_by_name,
                    fallback_annotator=fallback_annotator,
                )
        except Exception as exc:
            logger.error(
                "Frame box annotation persistence failed: %s", exc, exc_info=True
            )
            return Response(
                {"error": "Frame box annotation persistence failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = FrameBoxAnnotationSerializer(annotations, many=True)
        response_data: dict[str, Any] = {
            "status": "success",
            "upserted_count": len(annotations),
            "annotations": serializer.data,
        }
        if requested_video_id is not None:
            response_data["video_id"] = requested_video_id

        return Response(response_data, status=status.HTTP_200_OK)

    @staticmethod
    def _item_annotator(item: dict[str, Any], fallback_annotator: str) -> str:
        annotator = item.get("annotator")
        if annotator is None:
            return fallback_annotator
        return str(annotator or "").strip()

    def _delete_replaced_scope(
        self,
        *,
        validated_items: list[dict[str, Any]],
        source_by_name: dict[str, InformationSource],
        fallback_annotator: str,
    ) -> None:
        scope_filter = Q(pk__in=[])
        for item in validated_items:
            source_name = resolve_frame_information_source_name(
                item["information_source_name"]
            )
            scope_filter |= _annotation_scope_filter(
                frame_id=item["frame_id"],
                information_source_name=source_name,
                annotator=self._item_annotator(item, fallback_annotator),
            )
        FrameBoxAnnotation.objects.filter(scope_filter).delete()

    def _upsert_items(
        self,
        *,
        validated_items: list[dict[str, Any]],
        source_by_name: dict[str, InformationSource],
        fallback_annotator: str,
    ) -> list[FrameBoxAnnotation]:
        persisted: list[FrameBoxAnnotation] = []
        for item in validated_items:
            source_name = resolve_frame_information_source_name(
                item["information_source_name"]
            )
            annotator = self._item_annotator(item, fallback_annotator)
            annotation = self._resolve_existing_annotation(
                item=item,
                information_source_id=source_by_name[source_name].id,
                annotator=annotator,
            )
            annotation.frame_id = item["frame_id"]
            annotation.label_id = item["label_id"]
            annotation.value = item.get("value", True)
            annotation.float_value = item.get("float_value")
            annotation.x = item["x"]
            annotation.y = item["y"]
            annotation.width = item["width"]
            annotation.height = item["height"]
            annotation.image_width = item["image_width"]
            annotation.image_height = item["image_height"]
            annotation.information_source_id = source_by_name[source_name].id
            annotation.annotator = annotator
            annotation.model_meta_id = item.get("model_meta_id")
            annotation.external_annotation_id = item.get("external_annotation_id")
            annotation.save()
            persisted.append(annotation)
        return persisted

    @staticmethod
    def _resolve_existing_annotation(
        *,
        item: dict[str, Any],
        information_source_id: int,
        annotator: str,
    ) -> FrameBoxAnnotation:
        annotation_id = item.get("id")
        if annotation_id is not None:
            existing = FrameBoxAnnotation.objects.filter(pk=annotation_id).first()
            if existing is not None:
                return existing

        external_annotation_id = str(item.get("external_annotation_id") or "").strip()
        if external_annotation_id:
            existing = FrameBoxAnnotation.objects.filter(
                frame_id=item["frame_id"],
                information_source_id=information_source_id,
                annotator=annotator,
                external_annotation_id=external_annotation_id,
            ).first()
            if existing is not None:
                return existing

        return FrameBoxAnnotation()
