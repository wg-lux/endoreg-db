from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Protocol, cast

from django.db import transaction
from django.db.models import Q
from lx_dtypes.models.contracts.video_frame_box_annotations import (
    VideoFrameBoxAnnotationListResponsePayload,
    VideoFrameBoxAnnotationMutationResponsePayload,
    VideoFrameBoxAnnotationRequestPayload,
    VideoFrameBoxJsonObject,
    validate_video_frame_box_annotation_request,
    video_frame_box_json_safe_dict,
)
from pydantic import ValidationError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.label.annotation.frame_box import FrameBoxAnnotation
from endoreg_db.models.label.label import Label
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services.frame_annotation_workflow import (
    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
    resolve_frame_information_source_name,
    resolve_request_annotator,
)
from endoreg_db.serializers.label_video_segment.frame_box_annotation import (
    FrameBoxAnnotationBulkItemSerializer,
    FrameBoxAnnotationSerializer,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


class _FrameIdentity(Protocol):
    id: int
    pk: int
    video_id: int


class _InformationSourceIdentity(Protocol):
    pk: int
    name: str


class _RequestUser(Protocol):
    is_authenticated: bool
    username: str


class _MutableFrameBoxAnnotation(Protocol):
    frame_id: int
    label_id: int
    value: bool
    float_value: float | None
    x: float
    y: float
    width: float
    height: float
    image_width: int
    image_height: int
    information_source_id: int | None
    annotator: str
    model_meta_id: int | None
    external_annotation_id: str | None

    def save(self) -> None: ...


def _as_int(
    value: object,
    field_name: str,
) -> tuple[int | None, Response | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, int):
        return value, None
    if isinstance(value, str):
        try:
            return int(value), None
        except ValueError:
            pass
    return None, Response(
        {"error": f"{field_name} must be an integer."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _query_param(request: Request, name: str) -> object:
    return cast(object, request.query_params.get(name))


def _request_data(request: Request) -> object:
    return cast(object, request.data)


def _request_user_name(request: Request) -> str:
    user = cast(_RequestUser | None, getattr(request, "user", None))
    if user is not None and user.is_authenticated:
        return str(user.username)
    return ""


def _resolve_request_annotator(
    request: Request,
    requested_annotator: str | None,
) -> str:
    return resolve_request_annotator(request, requested_annotator)


def _json_object_list_excluding_none(value: object) -> list[VideoFrameBoxJsonObject]:
    if isinstance(value, Mapping):
        return []
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []

    items: list[VideoFrameBoxJsonObject] = []
    for item in cast(Iterable[object], value):
        if isinstance(item, Mapping):
            mapping = cast(Mapping[object, object], item)
            items.append(
                video_frame_box_json_safe_dict(
                    {
                        str(key): item_value
                        for key, item_value in mapping.items()
                        if item_value is not None
                    }
                )
            )
    return items


def _serializer_data(serializer: object) -> object:
    return cast(object, getattr(serializer, "data", []))


def _serializer_errors(serializer: object) -> object:
    return cast(object, getattr(serializer, "errors", {}))


def _validation_details(exc: ValidationError) -> list[VideoFrameBoxJsonObject]:
    details: list[VideoFrameBoxJsonObject] = []
    for error in exc.errors():
        details.append(video_frame_box_json_safe_dict(error))
    return details


def _item_int(item: Mapping[str, Any], key: str) -> int:
    return int(item[key])


def _item_optional_int(item: Mapping[str, Any], key: str) -> int | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    return int(value)


def _item_optional_float(item: Mapping[str, Any], key: str) -> float | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _item_str(item: Mapping[str, Any], key: str) -> str:
    return str(item[key])


def _item_optional_str(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


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


def _payload_has_annotation_list(raw_payload: object) -> bool:
    if isinstance(raw_payload, list):
        return True
    if not isinstance(raw_payload, Mapping):
        return False
    payload = cast(Mapping[object, object], raw_payload)
    return isinstance(payload.get("annotations"), list)


def _missing_annotations_response() -> Response:
    return Response(
        {"error": "Field 'annotations' is required when payload is an object."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _annotations_must_be_list_response() -> Response:
    return Response(
        {"error": "annotations must be a list."},
        status=status.HTTP_400_BAD_REQUEST,
    )


class FrameBoxAnnotationView(APIView):
    """
    Persist and list general box-based frame annotations.

    GET requires frame_id. POST accepts either a list of box annotations or an
    object with {frame_id, video_id, replace, annotations}.
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Response:
        frame_id, error = _as_int(_query_param(request, "frame_id"), "frame_id")
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
        frame_identity = cast(_FrameIdentity, frame)

        video_id, error = _as_int(_query_param(request, "video_id"), "video_id")
        if error is not None:
            return error
        if video_id is not None and frame_identity.video_id != video_id:
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

        information_source_name = _query_param(request, "information_source_name")
        if information_source_name is None:
            information_source_name = _query_param(request, "information_source")
        if information_source_name:
            queryset = queryset.filter(
                information_source__name=resolve_frame_information_source_name(
                    information_source_name
                )
            )

        annotator = _query_param(request, "annotator")
        if annotator is not None:
            queryset = queryset.filter(
                annotator=_resolve_request_annotator(request, str(annotator))
            )

        serializer = FrameBoxAnnotationSerializer(
            queryset.order_by("label__name", "id"),
            many=True,
        )
        annotations = _json_object_list_excluding_none(_serializer_data(serializer))
        response_payload = VideoFrameBoxAnnotationListResponsePayload(
            frame_id=int(frame_identity.id),
            video_id=int(frame_identity.video_id),
            annotations=annotations,
            count=len(annotations),
        )
        return Response(response_payload.to_response_dict(), status=status.HTTP_200_OK)

    def post(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Response:
        raw_payload = _request_data(request)
        if isinstance(raw_payload, Mapping) and "annotations" not in raw_payload:
            return _missing_annotations_response()
        if not isinstance(raw_payload, (Mapping, list)):
            return Response(
                {"error": "Payload must be a list or an object with 'annotations'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload_object = cast(object, raw_payload)
        if not _payload_has_annotation_list(payload_object):
            return _annotations_must_be_list_response()

        try:
            payload = validate_video_frame_box_annotation_request(payload_object)
        except ValidationError as exc:
            return Response(
                {"error": "Invalid payload.", "details": _validation_details(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if payload.replace and not payload.annotations:
            return self._delete_empty_replace_scope(
                frame_id=payload.frame_id,
                information_source_name=payload.resolved_information_source_name,
                annotator=payload.annotator,
                request=request,
            )

        normalized_items = self._normalized_annotation_items(payload)
        serializer = FrameBoxAnnotationBulkItemSerializer(
            data=normalized_items,
            many=True,
        )
        if not serializer.is_valid():
            return Response(
                {
                    "error": "Invalid data.",
                    "details": _serializer_errors(serializer),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_items = cast(list[VideoFrameBoxJsonObject], serializer.validated_data)
        frame_id = payload.frame_id
        if payload.replace and frame_id is None:
            frame_ids = {_item_int(item, "frame_id") for item in validated_items}
            if len(frame_ids) == 1:
                frame_id = next(iter(frame_ids))
            else:
                return Response(
                    {"error": "frame_id is required when replace=true."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return self._persist_validated_items(
            validated_items=validated_items,
            requested_video_id=payload.video_id,
            replace=payload.replace,
            request=request,
        )

    @staticmethod
    def _normalized_annotation_items(
        payload: VideoFrameBoxAnnotationRequestPayload,
    ) -> list[VideoFrameBoxJsonObject]:
        normalized_items: list[VideoFrameBoxJsonObject] = []
        for raw_item in payload.annotations:
            item: VideoFrameBoxJsonObject = dict(raw_item)
            if payload.frame_id is not None and item.get("frame_id") in {None, ""}:
                item["frame_id"] = payload.frame_id
            if not item.get("information_source_name"):
                item["information_source_name"] = (
                    payload.resolved_information_source_name
                    or DEFAULT_FRAME_INFORMATION_SOURCE_NAME
                )
            if item.get("annotator") in {None, ""} and payload.annotator is not None:
                item["annotator"] = payload.annotator
            normalized_items.append(item)
        return normalized_items

    def _delete_empty_replace_scope(
        self,
        *,
        frame_id: int | None,
        information_source_name: str | None,
        annotator: str | None,
        request: Request,
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
        frame_identity = cast(_FrameIdentity, frame)

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
        source_identity = cast(_InformationSourceIdentity, source)

        resolved_annotator = _resolve_request_annotator(request, annotator)
        deleted_count, _ = FrameBoxAnnotation.objects.filter(
            frame=frame,
            information_source__name=source_identity.name,
            annotator=resolved_annotator,
        ).delete()
        response_payload = VideoFrameBoxAnnotationMutationResponsePayload(
            video_id=int(frame_identity.video_id),
            upserted_count=0,
            deleted_count=deleted_count,
            annotations=[],
        )
        return Response(response_payload.to_response_dict(), status=status.HTTP_200_OK)

    def _persist_validated_items(
        self,
        *,
        validated_items: list[VideoFrameBoxJsonObject],
        requested_video_id: int | None,
        replace: bool,
        request: Request,
    ) -> Response:
        frame_ids = {_item_int(item, "frame_id") for item in validated_items}
        label_ids = {_item_int(item, "label_id") for item in validated_items}
        source_names = {
            resolve_frame_information_source_name(
                _item_str(item, "information_source_name")
            )
            for item in validated_items
        }
        model_meta_ids = {
            model_meta_id
            for item in validated_items
            if (model_meta_id := _item_optional_int(item, "model_meta_id")) is not None
        }

        frame_rows = cast(
            Iterable[Mapping[str, int]],
            Frame.objects.filter(id__in=frame_ids).values("id", "video_id"),
        )
        frame_video_by_id = {int(row["id"]): int(row["video_id"]) for row in frame_rows}
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
            cast(
                Iterable[int],
                Label.objects.filter(id__in=label_ids).values_list("id", flat=True),
            )
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
                cast(
                    Iterable[int],
                    ModelMeta.objects.filter(id__in=model_meta_ids).values_list(
                        "id",
                        flat=True,
                    ),
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

        source_by_name = self._source_by_name(source_names)
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

        fallback_annotator = _request_user_name(request)

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
                "Frame box annotation persistence failed: %s",
                exc,
                exc_info=True,
            )
            return Response(
                {"error": "Frame box annotation persistence failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = FrameBoxAnnotationSerializer(annotations, many=True)
        response_payload = VideoFrameBoxAnnotationMutationResponsePayload(
            video_id=requested_video_id,
            upserted_count=len(annotations),
            annotations=_json_object_list_excluding_none(_serializer_data(serializer)),
        )
        return Response(response_payload.to_response_dict(), status=status.HTTP_200_OK)

    @staticmethod
    def _source_by_name(
        source_names: set[str],
    ) -> dict[str, InformationSource]:
        sources = cast(
            Iterable[InformationSource],
            InformationSource.objects.filter(name__in=source_names),
        )
        source_by_name: dict[str, InformationSource] = {}
        for source in sources:
            source_identity = cast(_InformationSourceIdentity, source)
            source_by_name[source_identity.name] = source
        return source_by_name

    @staticmethod
    def _item_annotator(
        item: Mapping[str, Any],
        fallback_annotator: str,
    ) -> str:
        annotator = item.get("annotator")
        if annotator is None:
            return fallback_annotator
        return str(annotator or "").strip()

    def _delete_replaced_scope(
        self,
        *,
        validated_items: list[VideoFrameBoxJsonObject],
        source_by_name: dict[str, InformationSource],
        fallback_annotator: str,
    ) -> None:
        scope_filter = Q(pk__in=[])
        for item in validated_items:
            source_name = resolve_frame_information_source_name(
                _item_str(item, "information_source_name")
            )
            scope_filter |= _annotation_scope_filter(
                frame_id=_item_int(item, "frame_id"),
                information_source_name=source_name,
                annotator=self._item_annotator(item, fallback_annotator),
            )
        FrameBoxAnnotation.objects.filter(scope_filter).delete()

    def _upsert_items(
        self,
        *,
        validated_items: list[VideoFrameBoxJsonObject],
        source_by_name: dict[str, InformationSource],
        fallback_annotator: str,
    ) -> list[FrameBoxAnnotation]:
        persisted: list[FrameBoxAnnotation] = []
        for item in validated_items:
            source_name = resolve_frame_information_source_name(
                _item_str(item, "information_source_name")
            )
            source_id = model_pk(source_by_name[source_name])
            annotator = self._item_annotator(item, fallback_annotator)
            annotation_model = self._resolve_existing_annotation(
                item=item,
                information_source_id=source_id,
                annotator=annotator,
            )
            annotation = cast(_MutableFrameBoxAnnotation, annotation_model)
            annotation.frame_id = _item_int(item, "frame_id")
            annotation.label_id = _item_int(item, "label_id")
            annotation.value = bool(item.get("value", True))
            annotation.float_value = _item_optional_float(item, "float_value")
            annotation.x = float(item["x"])
            annotation.y = float(item["y"])
            annotation.width = float(item["width"])
            annotation.height = float(item["height"])
            annotation.image_width = _item_int(item, "image_width")
            annotation.image_height = _item_int(item, "image_height")
            annotation.information_source_id = source_id
            annotation.annotator = annotator
            annotation.model_meta_id = _item_optional_int(item, "model_meta_id")
            annotation.external_annotation_id = _item_optional_str(
                item,
                "external_annotation_id",
            )
            annotation.save()
            persisted.append(annotation_model)
        return persisted

    @staticmethod
    def _resolve_existing_annotation(
        *,
        item: Mapping[str, Any],
        information_source_id: int,
        annotator: str,
    ) -> FrameBoxAnnotation:
        annotation_id = _item_optional_int(item, "id")
        if annotation_id is not None:
            existing = FrameBoxAnnotation.objects.filter(pk=annotation_id).first()
            if existing is not None:
                return existing

        external_annotation_id = _item_optional_str(item, "external_annotation_id")
        if external_annotation_id:
            existing = FrameBoxAnnotation.objects.filter(
                frame_id=_item_int(item, "frame_id"),
                information_source_id=information_source_id,
                annotator=annotator,
                external_annotation_id=external_annotation_id,
            ).first()
            if existing is not None:
                return existing

        return FrameBoxAnnotation()
