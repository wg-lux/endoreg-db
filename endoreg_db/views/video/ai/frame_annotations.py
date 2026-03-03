import logging
import secrets
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import (
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    ModelMeta,
)
from endoreg_db.serializers.label_video_segment.frame_annotation_bulk import (
    FrameAnnotationBulkItemSerializer,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission, is_debug_mode

logger = logging.getLogger(__name__)
SUPPORTED_LABEL_STUDIO_ACTIONS = {"ANNOTATION_CREATED", "ANNOTATION_UPDATED"}


def _extract_webhook_token(request) -> str:
    """
    Extract webhook token from commonly used auth headers.
    """
    candidate_headers = (
        "X-Label-Studio-Webhook-Secret",
        "X-Label-Studio-Token",
        "X-Api-Key",
    )
    for header in candidate_headers:
        value = request.headers.get(header)
        if value:
            return str(value).strip()

    authorization = str(request.headers.get("Authorization", "")).strip()
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() in {"token", "bearer"}:
        return parts[1].strip()
    return authorization


def _verify_label_studio_webhook_secret(request) -> Response | None:
    """
    Validate webhook token against configured shared secret.
    """
    expected_secret = str(
        getattr(settings, "LABEL_STUDIO_WEBHOOK_SECRET", "") or ""
    ).strip()
    if not expected_secret:
        if is_debug_mode():
            logger.warning(
                "LABEL_STUDIO_WEBHOOK_SECRET is not configured; allowing webhook in debug mode."
            )
            return None
        return Response(
            {"error": "Label Studio webhook secret is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    provided_secret = _extract_webhook_token(request)
    if not provided_secret or not secrets.compare_digest(
        provided_secret, expected_secret
    ):
        return Response(
            {"error": "Invalid Label Studio webhook token."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _resolve_label_studio_annotator(completed_by: Any) -> str:
    """
    Resolve Label Studio's completed_by payload to a stable annotator string.
    """
    if isinstance(completed_by, dict):
        username = completed_by.get("username")
        if isinstance(username, str) and username.strip():
            return username.strip()
        email = completed_by.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
        completed_by = completed_by.get("id")

    if isinstance(completed_by, str) and completed_by.strip():
        stripped = completed_by.strip()
        if not stripped.isdigit():
            return stripped
        completed_by = stripped

    try:
        user_id = int(completed_by)
    except (TypeError, ValueError):
        return "label_studio"

    user_model = get_user_model()
    username = (
        user_model.objects.filter(pk=user_id).values_list("username", flat=True).first()
    )
    if isinstance(username, str) and username.strip():
        return username.strip()
    return f"label_studio_user_{user_id}"


def _extract_choice_names_and_float_value(
    result_items: list[dict[str, Any]],
) -> tuple[list[str], float | None]:
    """
    Parse choice labels and an optional numeric confidence from annotation.result.
    """
    choice_names: list[str] = []
    float_value: float | None = None

    for result_item in result_items:
        if not isinstance(result_item, dict):
            continue
        value = result_item.get("value")
        if not isinstance(value, dict):
            continue

        choices = value.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, str) and choice.strip():
                    choice_names.append(choice.strip())

        if float_value is None:
            for numeric_key in ("rating", "number"):
                raw_number = value.get(numeric_key)
                if raw_number is None or raw_number == "":
                    continue
                try:
                    float_value = float(raw_number)
                    break
                except (TypeError, ValueError):
                    continue

    deduplicated_choices = list(dict.fromkeys(choice_names))
    return deduplicated_choices, float_value


def _resolve_label_ids_from_choice_names(
    choice_names: list[str],
) -> tuple[dict[str, int], list[str]]:
    """
    Resolve Label names from Label Studio choices to local label IDs.
    """
    label_id_by_choice = {
        label.name: label.id for label in Label.objects.filter(name__in=choice_names)
    }

    unresolved_choice_names = [
        name for name in choice_names if name not in label_id_by_choice
    ]
    for unresolved_name in unresolved_choice_names:
        match = Label.objects.filter(name__iexact=unresolved_name).only("id").first()
        if match is not None:
            label_id_by_choice[unresolved_name] = match.id

    missing_choice_names = sorted(
        name for name in choice_names if name not in label_id_by_choice
    )
    return label_id_by_choice, missing_choice_names


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


class LabelStudioWebhookReceiverView(APIView):
    """
    Receives raw Label Studio webhook payloads and translates them into the
    internal frame annotation bulk-upsert contract.
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        token_error = _verify_label_studio_webhook_secret(request)
        if token_error is not None:
            return token_error

        payload = request.data
        if not isinstance(payload, dict):
            return Response(
                {"error": "Payload must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = str(payload.get("action", "")).strip()
        if action not in SUPPORTED_LABEL_STUDIO_ACTIONS:
            return Response(
                {"status": "ignored", "action": action or "UNKNOWN"},
                status=status.HTTP_200_OK,
            )

        annotation = payload.get("annotation")
        if not isinstance(annotation, dict):
            return Response(
                {"error": "Missing or invalid 'annotation' object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = payload.get("task")
        if not isinstance(task, dict):
            return Response(
                {"error": "Missing or invalid 'task' object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        task_data = task.get("data")
        if not isinstance(task_data, dict):
            return Response(
                {"error": "Missing or invalid 'task.data' object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        frame_id_raw = task_data.get("frame_id")
        try:
            frame_id = int(frame_id_raw)
        except (TypeError, ValueError):
            return Response(
                {"error": "task.data.frame_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_video_id = None
        video_id_raw = task_data.get("video_id")
        if video_id_raw is not None:
            try:
                requested_video_id = int(video_id_raw)
            except (TypeError, ValueError):
                return Response(
                    {"error": "task.data.video_id must be an integer when provided."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        result_items = annotation.get("result")
        if not isinstance(result_items, list):
            return Response(
                {"error": "annotation.result must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        choice_names, float_value = _extract_choice_names_and_float_value(result_items)
        if not choice_names:
            if bool(annotation.get("was_cancelled")):
                return Response(
                    {"status": "ignored", "reason": "annotation_cancelled"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": "No label choices found in annotation.result."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label_id_by_choice, missing_choice_names = _resolve_label_ids_from_choice_names(
            choice_names
        )
        if missing_choice_names:
            return Response(
                {
                    "error": "Unknown label names in annotation.result choices.",
                    "details": {"unknown_label_names": missing_choice_names},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        information_source_name = str(
            getattr(
                settings, "LABEL_STUDIO_INFORMATION_SOURCE_NAME", "manual_annotation"
            )
            or "manual_annotation"
        ).strip()
        if not information_source_name:
            information_source_name = "manual_annotation"

        annotator = _resolve_label_studio_annotator(annotation.get("completed_by"))
        annotation_id = annotation.get("id")
        external_annotation_id = (
            None if annotation_id is None else str(annotation_id).strip()
        )

        upsert_items: list[dict[str, Any]] = []
        for choice_name in choice_names:
            upsert_items.append(
                {
                    "frame_id": frame_id,
                    "label_id": label_id_by_choice[choice_name],
                    "value": True,
                    "float_value": float_value,
                    "information_source_name": information_source_name,
                    "annotator": annotator,
                    "external_annotation_id": external_annotation_id,
                }
            )

        response = _build_bulk_upsert_response(
            annotation_items=upsert_items,
            requested_video_id=requested_video_id,
            fallback_annotator=annotator,
        )

        if response.status_code == status.HTTP_200_OK and isinstance(
            response.data, dict
        ):
            response.data["action"] = action
            if external_annotation_id is not None:
                response.data["external_annotation_id"] = external_annotation_id
        return response
