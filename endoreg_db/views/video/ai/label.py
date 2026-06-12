from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol, TypeAlias, cast

from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from lx_dtypes.models.contracts.video_ai_labels import (
    VideoAiHuggingFaceModelPayload,
    VideoAiJsonObject,
    VideoAiLabelMutationResponsePayload,
    VideoAiLabelPayload,
    VideoAiLabelSetPayload,
    VideoAiPredictionJobPayload,
    VideoAiPredictionModelListPayload,
    VideoAiPredictionModelMetaPayload,
    VideoAiRerunPredictionRequestPayload,
    VideoAiRerunPredictionResponsePayload,
    validate_video_ai_label_name_payload,
    validate_video_ai_label_rename_payload,
    validate_video_ai_rerun_prediction_request,
    video_ai_json_safe_dict,
)
from pydantic import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.services.video_temporal_inference import (
    TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD,
    TemporalInferenceConfigError,
    dispatch_video_temporal_inference,
    extract_temporal_options,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)

DEFAULT_HF_SEGMENTATION_MODEL_ID = "wg-lux/colo_segmentation_RegNetX800MF_base"
DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"
DEFAULT_SEGMENTATION_LABELSET_NAME = "multilabel_classification_colonoscopy_default"

VideoAiResponseData: TypeAlias = VideoAiJsonObject | list[VideoAiJsonObject]


class _LabelSource(Protocol):
    pk: int
    name: str


class _MutableLabelSource(_LabelSource, Protocol):
    def save(self) -> None: ...


class _LabelRelation(Protocol):
    def all(self) -> Iterable[_LabelSource]: ...


class _LabelQuerySet(Protocol):
    def order_by(self, *fields: str) -> Iterable[_LabelSource]: ...


class _LabelManagerSource(Protocol):
    def all(self) -> _LabelQuerySet: ...


class _LabelSetSource(Protocol):
    pk: int
    name: str
    version: int
    description: str | None
    labels: _LabelRelation


class _AiModelSource(Protocol):
    pk: int
    name: str
    active_meta_id: int | None
    metadata_versions: _ModelMetaQuerySet

    def get_latest_version(self) -> _ModelMetaSource: ...


class _AiModelManagerSource(Protocol):
    def get(self, **kwargs: object) -> _AiModelSource: ...


class _ModelMetaSource(Protocol):
    pk: int
    name: str
    version: object
    description: str | None
    model: _AiModelSource
    labelset: _LabelSetSource
    weights: object | None


class _ModelMetaQuerySet(Protocol):
    def select_related(self, *fields: str) -> "_ModelMetaQuerySet": ...

    def all(self) -> "_ModelMetaQuerySet": ...

    def order_by(self, *fields: str) -> Iterable[_ModelMetaSource]: ...

    def get(self, **kwargs: object) -> _ModelMetaSource: ...


class _ModelMetaManagerSource(Protocol):
    def select_related(self, *fields: str) -> _ModelMetaQuerySet: ...


def _request_payload_data(request: Request) -> VideoAiJsonObject:
    return video_ai_json_safe_dict(cast(object, request.data))


def _validation_error_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if errors:
        return errors[0].get("msg", str(exc))
    return str(exc)


def _error_response(
    error: str,
    *,
    status_code: int,
    error_type: str | None = None,
) -> Response:
    payload: VideoAiJsonObject = {"error": error}
    if error_type is not None:
        payload["error_type"] = error_type
    return Response(payload, status=status_code)


def _missing_required_field_response(field_name: str) -> Response:
    return _error_response(
        f"Field '{field_name}' is required",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _serialize_label_payload(label: _LabelSource) -> VideoAiLabelPayload:
    return VideoAiLabelPayload(id=int(label.pk), name=label.name)


def _serialize_label_set(label_set: LabelSet) -> VideoAiLabelSetPayload:
    label_set_source = cast(_LabelSetSource, label_set)
    labels = sorted(label_set_source.labels.all(), key=lambda label: label.name)
    return VideoAiLabelSetPayload(
        id=int(label_set_source.pk),
        name=label_set_source.name,
        version=int(label_set_source.version),
        description=label_set_source.description or "",
        label_count=len(labels),
        labels=[_serialize_label_payload(label) for label in labels],
    )


def _serialize_model_meta(
    model_meta_source: _ModelMetaSource,
) -> VideoAiPredictionModelMetaPayload:
    ai_model = model_meta_source.model
    label_set = model_meta_source.labelset
    return VideoAiPredictionModelMetaPayload(
        id=int(model_meta_source.pk),
        name=model_meta_source.name,
        version=str(model_meta_source.version),
        description=model_meta_source.description or "",
        model_name=ai_model.name,
        ai_model_id=int(ai_model.pk),
        labelset_name=label_set.name,
        labelset_version=int(label_set.version),
        labelset_id=int(label_set.pk),
        weights_available=bool(model_meta_source.weights),
        is_active=ai_model.active_meta_id == model_meta_source.pk,
    )


def _prediction_segments_for_video(
    video: VideoFile,
) -> QuerySet[LabelVideoSegment]:
    return LabelVideoSegment.objects.filter(video_file=video).filter(
        Q(prediction_meta__isnull=False) | Q(source__name="prediction")
    )


def _resolve_prediction_model_meta(
    payload: VideoAiRerunPredictionRequestPayload,
) -> ModelMeta:
    if payload.model_meta_id is not None:
        return cast(
            ModelMeta,
            cast(_ModelMetaManagerSource, cast(object, ModelMeta.objects))
            .select_related("model", "labelset")
            .get(pk=payload.model_meta_id),
        )

    hf_model_id = payload.resolved_huggingface_model_id
    if hf_model_id is not None:
        return ModelMeta.setup_default_from_huggingface(
            model_id=hf_model_id,
            labelset_name=payload.resolved_labelset_name
            or DEFAULT_SEGMENTATION_LABELSET_NAME,
            labelset_version=payload.labelset_version,
        )

    model_name = payload.model_name or DEFAULT_SEGMENTATION_MODEL_NAME
    ai_model = cast(
        _AiModelManagerSource,
        AiModel.objects,
    ).get(name=model_name)
    if payload.model_meta_version is not None:
        return cast(
            ModelMeta,
            ai_model.metadata_versions.select_related("model", "labelset").get(
                version=payload.model_meta_version
            ),
        )
    return cast(ModelMeta, ai_model.get_latest_version())


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def label_list(request: Request) -> Response:
    """
    List all annotation labels used for video segments.
    """
    try:
        labels = cast(_LabelManagerSource, Label.objects).all().order_by("name")
        payload = [
            _serialize_label_payload(label).model_dump(mode="json") for label in labels
        ]
        return Response(payload, status=status.HTTP_200_OK)
    except Exception as exc:
        logger.error("Error fetching labels: %s", exc)
        return _error_response(
            "Failed to fetch labels",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def prediction_model_list(request: Request) -> Response:
    """
    List locally registered video prediction ModelMeta records and known
    Hugging Face defaults that can be materialized on demand.
    """
    try:
        model_metas = (
            cast(
                _ModelMetaManagerSource,
                cast(object, ModelMeta.objects),
            )
            .select_related("model", "labelset")
            .all()
            .order_by("model__name", "name", "-version", "id")
        )
        payload = VideoAiPredictionModelListPayload(
            models=[_serialize_model_meta(model_meta) for model_meta in model_metas],
            default_huggingface_model_id=DEFAULT_HF_SEGMENTATION_MODEL_ID,
            default_model_name=DEFAULT_SEGMENTATION_MODEL_NAME,
            default_labelset_name=DEFAULT_SEGMENTATION_LABELSET_NAME,
            huggingface_models=[
                VideoAiHuggingFaceModelPayload(
                    model_id=DEFAULT_HF_SEGMENTATION_MODEL_ID,
                    label="Colonoscopy segmentation RegNetX800MF",
                    labelset_name=DEFAULT_SEGMENTATION_LABELSET_NAME,
                )
            ],
        )
        return Response(payload.model_dump(mode="json"), status=status.HTTP_200_OK)
    except Exception:
        logger.exception("Error fetching video prediction models")
        return _error_response(
            "Failed to fetch video prediction models",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def rerun_prediction_segments(
    request: Request,
    pk: int,
) -> Response:
    """
    Rerun temporal prediction segment materialization for a single video.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    try:
        payload = validate_video_ai_rerun_prediction_request(
            _request_payload_data(request)
        )
    except ValidationError as exc:
        return _error_response(
            _validation_error_message(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
            error_type="invalid_options",
        )

    try:
        model_meta = _resolve_prediction_model_meta(payload)
    except (AiModel.DoesNotExist, ModelMeta.DoesNotExist, ValueError) as exc:
        return _error_response(
            str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
            error_type="model_resolution_failed",
        )
    except Exception as exc:
        logger.exception("Could not prepare prediction model for video %s", pk)
        return _error_response(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="model_preparation_failed",
        )

    video_id = model_pk(video)
    model_meta_id = model_pk(model_meta)
    try:
        dispatch_result = dispatch_video_temporal_inference(
            video_id=video_id,
            model_meta_id=model_meta_id,
            replace_prediction_segments=payload.replace_prediction_segments,
            delete_frames_after=payload.delete_frames_after,
            ocr_frame_fraction=payload.ocr_frame_fraction,
            ocr_cap=payload.ocr_cap,
            temporal_options=extract_temporal_options(
                payload.to_temporal_options_payload()
            ),
            test_run=payload.test_run,
            n_test_frames=payload.n_test_frames,
        )
    except TemporalInferenceConfigError as exc:
        return _error_response(
            str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
            error_type="invalid_temporal_options",
        )
    except Exception as exc:
        logger.exception("Could not dispatch temporal inference for video %s", pk)
        return _error_response(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="prediction_dispatch_failed",
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
    response_payload = VideoAiRerunPredictionResponsePayload(
        success=dispatch_result.status in queued_statuses or pending_after_rebuild,
        status=dispatch_result.status,
        queued=dispatch_result.status in queued_statuses,
        pending=pending_after_rebuild,
        video_id=video_id,
        model_meta=_serialize_model_meta(cast(_ModelMetaSource, model_meta)),
        job=VideoAiPredictionJobPayload(
            task_id=dispatch_result.task_id,
            history_id=dispatch_result.history_id,
            mode=dispatch_result.mode,
            queue=dispatch_result.queue,
        ),
        deleted_prediction_segments=dispatch_result.deleted_prediction_segments,
        prediction_segments_count=prediction_segments_count,
        reason=dispatch_result.reason,
        message=dispatch_result.message,
        blocked_by_history_id=dispatch_result.blocked_by_history_id,
    )

    return Response(response_payload.to_response_dict(), status=response_status)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def label_set_list(request: Request) -> Response:
    """
    List annotation label groups as LabelSet records.
    """
    try:
        label_sets = cast(
            Iterable[LabelSet],
            LabelSet.objects.prefetch_related("labels")
            .all()
            .order_by("name", "-version", "id"),
        )
        payload = [
            _serialize_label_set(label_set).model_dump(mode="json")
            for label_set in label_sets
        ]
        return Response(payload, status=status.HTTP_200_OK)
    except Exception:
        logger.exception("Error fetching label sets")
        return _error_response(
            "Failed to fetch label sets",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def add_label(request: Request) -> Response:
    try:
        payload = validate_video_ai_label_name_payload(_request_payload_data(request))
    except ValidationError:
        return _missing_required_field_response("name")

    try:
        label_model, created = Label.get_or_create_from_name(payload.name)
        label = cast(_LabelSource, label_model)

        response_payload = VideoAiLabelMutationResponsePayload(
            success="label added to database" if created else "label already existed",
            id=int(label.pk),
            name=label.name,
        )
        return Response(
            response_payload.to_response_dict(),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    except Exception as exc:
        logger.error("Error creating label: %s", exc)
        return _error_response(
            "Failed to create label",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["DELETE"])
def delete_label(request: Request) -> Response:
    try:
        payload = validate_video_ai_label_name_payload(_request_payload_data(request))
    except ValidationError:
        return _missing_required_field_response("name")

    try:
        deleted_count, _ = Label.objects.filter(name=payload.name).delete()
        if deleted_count < 1:
            return _error_response(
                f"Label '{payload.name}' not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        response_payload = VideoAiLabelMutationResponsePayload(
            success=f"label {payload.name} deleted"
        )
        return Response(response_payload.to_response_dict(), status=status.HTTP_200_OK)
    except Exception as exc:
        logger.error("Error deleting label: %s", exc)
        return _error_response(
            "Failed to delete label",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["PATCH", "POST"])
@permission_classes([EnvironmentAwarePermission])
def update_label(request: Request) -> Response:
    """
    Update/rename a label.
    """
    try:
        payload = validate_video_ai_label_rename_payload(_request_payload_data(request))
    except ValidationError as exc:
        field_names = {error.get("loc", ("",))[0] for error in exc.errors()}
        if "name_old" in field_names:
            return _missing_required_field_response("name_old")
        if "name" in field_names:
            return _missing_required_field_response("name")
        return _error_response(
            _validation_error_message(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        try:
            label_model = Label.objects.get(name=payload.name_old)
        except Label.DoesNotExist:
            return _error_response(
                f"Label '{payload.name_old}' not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        label = cast(_MutableLabelSource, label_model)
        if Label.objects.filter(name=payload.name).exclude(pk=label.pk).exists():
            return _error_response(
                f"Label '{payload.name}' already exists",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        label.name = payload.name
        label.save()

        response_payload = VideoAiLabelMutationResponsePayload(
            success=f"Label '{payload.name_old}' renamed to '{payload.name}'",
            id=int(label.pk),
            name=label.name,
        )
        return Response(response_payload.to_response_dict(), status=status.HTTP_200_OK)
    except Exception as exc:
        logger.error(
            "Error updating label '%s' -> '%s': %s",
            payload.name_old,
            payload.name,
            exc,
        )
        return _error_response(
            "Failed to update label",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
