# endoreg_db/views/media/label_media.py
import logging
from typing import Any

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status

from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.serializers.label_video_segment.label import LabelSerializer
from endoreg_db.services.video_temporal_inference import (
    TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD,
    TemporalInferenceConfigError,
    dispatch_video_temporal_inference,
    extract_temporal_options,
)
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission

# from rest_framework.permissions import IsAuthenticated
# from endoreg_db.authz.permissions import PolicyPermission

logger = logging.getLogger(__name__)

DEFAULT_HF_SEGMENTATION_MODEL_ID = "wg-lux/colo_segmentation_RegNetX800MF_base"
DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"
DEFAULT_SEGMENTATION_LABELSET_NAME = "multilabel_classification_colonoscopy_default"


def _serialize_label_set(label_set: LabelSet) -> dict[str, object]:
    labels = sorted(label_set.labels.all(), key=lambda label: label.name)
    return {
        "id": label_set.pk,
        "name": label_set.name,
        "version": label_set.version,
        "description": label_set.description or "",
        "label_count": len(labels),
        "labels": [{"id": label.pk, "name": label.name} for label in labels],
    }


def _serialize_model_meta(model_meta: ModelMeta) -> dict[str, object]:
    ai_model = model_meta.model
    label_set = model_meta.labelset
    return {
        "id": model_meta.pk,
        "name": model_meta.name,
        "version": str(model_meta.version),
        "description": model_meta.description or "",
        "model_name": ai_model.name,
        "ai_model_id": ai_model.pk,
        "labelset_name": label_set.name,
        "labelset_version": label_set.version,
        "labelset_id": label_set.pk,
        "weights_available": bool(model_meta.weights),
        "is_active": ai_model.active_meta_id == model_meta.pk,
    }


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _prediction_segments_for_video(video: VideoFile):
    return LabelVideoSegment.objects.filter(video_file=video).filter(
        Q(prediction_meta__isnull=False) | Q(source__name="prediction")
    )


def _resolve_prediction_model_meta(payload: dict[str, Any]) -> ModelMeta:
    model_meta_id = payload.get("model_meta_id")
    try:
        if model_meta_id not in (None, ""):
            return ModelMeta.objects.select_related("model", "labelset").get(
                pk=int(str(model_meta_id))
            )
    except ValueError as e:
        logger.info(f"No id specified. {e} Resolving by different method.")

    hf_model_id = (
        payload.get("hf_model_id")
        or payload.get("huggingface_model_id")
        or payload.get("model_id")
    )
    if hf_model_id:
        labelset_name = (
            payload.get("labelset_name")
            or payload.get("label_set_name")
            or DEFAULT_SEGMENTATION_LABELSET_NAME
        )
        labelset_version = payload.get("labelset_version")
        return ModelMeta.setup_default_from_huggingface(
            model_id=str(hf_model_id).strip(),
            labelset_name=str(labelset_name).strip(),
            labelset_version=labelset_version,
        )

    model_name = str(
        payload.get("model_name") or DEFAULT_SEGMENTATION_MODEL_NAME
    ).strip()
    model_meta_version = payload.get("model_meta_version")
    ai_model = AiModel.objects.get(name=model_name)
    if model_meta_version not in (None, ""):
        return ai_model.metadata_versions.select_related("model", "labelset").get(
            version=str(model_meta_version)
        )
    return ai_model.get_latest_version()


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
# or: @permission_classes([IsAuthenticated, PolicyPermission])
def label_list(request) -> Response:
    """
    List all annotation labels used for video segments.

    GET /api/media/labels/
    Response:
    [
      { "id": 1, "name": "polyp" },
      ...
    ]
    """
    try:
        labels = Label.objects.all().order_by("name")
        serializer = LabelSerializer(labels, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error fetching labels: {e}")
        return Response(
            {"error": "Failed to fetch labels"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def prediction_model_list(request) -> Response:
    """
    List locally registered video prediction ModelMeta records and known
    Hugging Face defaults that can be materialized on demand.
    """
    try:
        model_metas = (
            ModelMeta.objects.select_related("model", "labelset")
            .all()
            .order_by("model__name", "name", "-version", "id")
        )
        return Response(
            {
                "models": [
                    _serialize_model_meta(model_meta) for model_meta in model_metas
                ],
                "default_huggingface_model_id": DEFAULT_HF_SEGMENTATION_MODEL_ID,
                "default_model_name": DEFAULT_SEGMENTATION_MODEL_NAME,
                "default_labelset_name": DEFAULT_SEGMENTATION_LABELSET_NAME,
                "huggingface_models": [
                    {
                        "model_id": DEFAULT_HF_SEGMENTATION_MODEL_ID,
                        "label": "Colonoscopy segmentation RegNetX800MF",
                        "labelset_name": DEFAULT_SEGMENTATION_LABELSET_NAME,
                    }
                ],
            },
            status=status.HTTP_200_OK,
        )
    except Exception:
        logger.exception("Error fetching video prediction models")
        return Response(
            {"error": "Failed to fetch video prediction models"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def rerun_prediction_segments(request, pk: int) -> Response:
    """
    Rerun pipe_1 prediction segment materialization for a single video.

    Body accepts one of:
    - model_meta_id: existing ModelMeta primary key
    - hf_model_id / huggingface_model_id / model_id: Hugging Face repository id
    - model_name (+ optional model_meta_version)
    """
    video = get_object_or_404(VideoFile, pk=pk)
    payload = request.data if hasattr(request.data, "get") else {}

    try:
        model_meta = _resolve_prediction_model_meta(payload)
    except (AiModel.DoesNotExist, ModelMeta.DoesNotExist, ValueError) as exc:
        return Response(
            {"error": str(exc), "error_type": "model_resolution_failed"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Could not prepare prediction model for video %s", pk)
        return Response(
            {"error": str(exc), "error_type": "model_preparation_failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    replace_prediction_segments = _as_bool(
        payload.get("replace_prediction_segments"), default=True
    )
    delete_frames_after = _as_bool(payload.get("delete_frames_after"), default=True)
    try:
        ocr_frame_fraction = float(payload.get("ocr_frame_fraction") or 0.001)
        ocr_cap = int(payload.get("ocr_cap") or 10)
    except (TypeError, ValueError):
        return Response(
            {"error": "Invalid OCR options.", "error_type": "invalid_options"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    test_run = _as_bool(payload.get("test_run"), default=False)
    try:
        n_test_frames = int(payload.get("n_test_frames") or 10)
    except (TypeError, ValueError):
        return Response(
            {
                "error": "Invalid test run options.",
                "error_type": "invalid_options",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        dispatch_result = dispatch_video_temporal_inference(
            video_id=video.pk,
            model_meta_id=model_meta.pk,
            replace_prediction_segments=replace_prediction_segments,
            delete_frames_after=delete_frames_after,
            ocr_frame_fraction=ocr_frame_fraction,
            ocr_cap=ocr_cap,
            temporal_options=extract_temporal_options(payload),
            test_run=test_run,
            n_test_frames=n_test_frames,
        )
    except TemporalInferenceConfigError as exc:
        return Response(
            {"error": str(exc), "error_type": "invalid_temporal_options"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Could not dispatch temporal inference for video %s", pk)
        return Response(
            {"error": str(exc), "error_type": "prediction_dispatch_failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    prediction_segments_count = (
        dispatch_result.prediction_segments_count
        if dispatch_result.prediction_segments_count is not None
        else _prediction_segments_for_video(video).count()
    )
    response_status: int = status.HTTP_202_ACCEPTED
    if dispatch_result.status == "completed":
        response_status = status.HTTP_200_OK
    elif dispatch_result.status == "busy":
        response_status = status.HTTP_409_CONFLICT
    elif dispatch_result.status == "failed":
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR

    queued_statuses = {"queued", "already_queued", "completed"}
    pending_after_rebuild = (
        dispatch_result.status == TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD
    )
    response_payload = {
        "success": dispatch_result.status in queued_statuses or pending_after_rebuild,
        "status": dispatch_result.status,
        "queued": dispatch_result.status in queued_statuses,
        "pending": pending_after_rebuild,
        "video_id": video.pk,
        "model_meta": _serialize_model_meta(model_meta),
        "job": {
            "task_id": dispatch_result.task_id,
            "history_id": dispatch_result.history_id,
            "mode": dispatch_result.mode,
            "queue": dispatch_result.queue,
        },
        "deleted_prediction_segments": dispatch_result.deleted_prediction_segments,
        "prediction_segments_count": prediction_segments_count,
    }
    if dispatch_result.reason:
        response_payload["reason"] = dispatch_result.reason
    if dispatch_result.message:
        response_payload["message"] = dispatch_result.message
    if dispatch_result.blocked_by_history_id is not None:
        response_payload["blocked_by_history_id"] = (
            dispatch_result.blocked_by_history_id
        )

    return Response(
        response_payload,
        status=response_status,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def label_set_list(request) -> Response:
    """
    List annotation label groups as LabelSet records.

    GET /api/media/videos/label-sets/list/
    Response:
    [
      {
        "id": 1,
        "name": "multilabel_classification_colonoscopy_default",
        "version": 2,
        "description": "",
        "label_count": 11,
        "labels": [{ "id": 1, "name": "polyp" }]
      }
    ]
    """
    try:
        label_sets = (
            LabelSet.objects.prefetch_related("labels")
            .all()
            .order_by("name", "-version", "id")
        )
        return Response(
            [_serialize_label_set(label_set) for label_set in label_sets],
            status=status.HTTP_200_OK,
        )
    except Exception:
        logger.exception("Error fetching label sets")
        return Response(
            {"error": "Failed to fetch label sets"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def add_label(request) -> Response:
    try:
        name = request.data.get("name")
        if not name:
            return Response(
                {"error": "Field 'name' is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label, created = Label.get_or_create_from_name(name)

        return Response(
            {
                "success": (
                    "label added to database" if created else "label already existed"
                ),
                "id": label.id,
                "name": label.name,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Error creating label: {e}")
        return Response(
            {"error": "Failed to create label"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["DELETE"])
def delete_label(request) -> Response:
    try:
        name = request.data.get("name")
        if not name:
            return Response(
                {"error": "Field 'name' is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        Label.delete(name)
        if isinstance(Label.get_or_create_from_name(name), Label):
            return Response(
                {"error": "Field not deleted"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        else:
            return Response(
                {"success": f"label {name} deleted"}, status=status.HTTP_200_OK
            )
    except Exception as e:
        logger.error(f"Error creating label: {e}")
        return Response(
            {"error": "Failed to create label"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["PATCH", "POST"])
@permission_classes([EnvironmentAwarePermission])
def update_label(request) -> Response:
    """
    Update/rename a label.

    Body:
    {
      "name_old": "polyp_old",
      "name": "polyp"
    }
    """
    name_old = request.data.get("name_old")
    new_name = request.data.get("name")

    if not name_old:
        return Response(
            {"error": "Field 'name_old' is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not new_name:
        return Response(
            {"error": "Field 'name' is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        try:
            label = Label.objects.get(name=name_old)
        except Label.DoesNotExist:
            return Response(
                {"error": f"Label '{name_old}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Optional: handle duplicate target names
        if Label.objects.filter(name=new_name).exclude(pk=label.pk).exists():
            return Response(
                {"error": f"Label '{new_name}' already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label.name = new_name
        label.save()

        return Response(
            {
                "success": f"Label '{name_old}' renamed to '{new_name}'",
                "id": label.id,
                "name": label.name,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Error updating label '{name_old}' → '{new_name}': {e}")
        return Response(
            {"error": "Failed to update label"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
