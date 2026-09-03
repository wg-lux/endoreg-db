# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import json
import threading
import traceback
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone

from endoreg_db.models.aidataset.aidataset import AIDataSet, AIModelTrainingRun
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
)
from endoreg_db.services.lifecycle_state_machine import (
    OperationLifecycleEvent,
    OperationLifecycleState,
    reduce_operation_lifecycle,
)
from endoreg_db.services.video_files._frames._manage_frame_range import (
    extract_frame_range_to_directory,
)
from endoreg_db.schemas import (
    validate_ai_model_training_artifact_paths,
    validate_ai_model_training_command_kwargs,
    validate_ai_model_training_request_payload,
    validate_ai_model_training_result_payload,
)
from endoreg_db.services.video_files import (
    get_or_create_video_state,
    get_video_frame_dir_path,
)
from endoreg_db.utils.ai.multilabel_dataset_builder import (
    ANNOTATION_SOURCE_SCOPE_ALL,
    AnnotationSourceScope,
    normalize_annotation_source_scope,
    uses_frame_annotations,
    uses_segment_annotations,
)
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    ensure_directory,
    safe_rmtree,
)

MODEL_TRAINING_TARGET_IMAGE_MULTILABEL = "image_multilabel"
MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR = "phi_region_detector"
MODEL_TRAINING_SERVER_INSTANCE_ID = uuid4().hex
MODEL_TRAINING_LOST_TIMEOUT = timedelta(hours=25)
MODEL_TRAINING_DISPATCH_TIMEOUT = timedelta(minutes=10)
MODEL_TRAINING_LEASE_SECONDS = 30 * 60
MODEL_TRAINING_HEARTBEAT_SECONDS = 60
MODEL_TRAINING_RETRY_BASE_SECONDS = 60
MODEL_TRAINING_RETRY_MAX_SECONDS = 60 * 60
DEFAULT_MODEL_TRAINING_STAGING_ROOT = Path("/mnt/fast-nvme-cache/endoreg-training")


def _validated_model_training_run_updates(
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Canonicalize JSONField values before a direct queryset update.

    QuerySet.update() intentionally bypasses AIModelTrainingRun.clean()/save().
    Keep the asynchronous worker's atomic status updates while applying the same
    persisted-JSON contracts whenever one of the model's JSON fields is present.
    """
    normalized = dict(updates)
    if "request_payload" in normalized:
        normalized["request_payload"] = validate_ai_model_training_request_payload(
            normalized["request_payload"]
        )
    if "command_kwargs" in normalized:
        normalized["command_kwargs"] = validate_ai_model_training_command_kwargs(
            normalized["command_kwargs"]
        )
    if "result" in normalized:
        normalized["result"] = validate_ai_model_training_result_payload(
            normalized["result"]
        )
    if "artifact_paths" in normalized:
        normalized["artifact_paths"] = validate_ai_model_training_artifact_paths(
            normalized["artifact_paths"]
        )
    return normalized


def _update_model_training_run(run_uuid: UUID, **updates: Any) -> int:
    normalized = _validated_model_training_run_updates(updates)
    return AIModelTrainingRun.objects.filter(run_id=run_uuid).update(**normalized)


_MODEL_TRAINING_STATES: dict[str, OperationLifecycleState] = {
    AIModelTrainingRun.STATUS_QUEUED: OperationLifecycleState.QUEUED,
    AIModelTrainingRun.STATUS_RUNNING: OperationLifecycleState.RUNNING,
    AIModelTrainingRun.STATUS_RETRY_WAIT: OperationLifecycleState.RETRY_WAIT,
    AIModelTrainingRun.STATUS_COMPLETED: OperationLifecycleState.SUCCEEDED,
    AIModelTrainingRun.STATUS_FAILED: OperationLifecycleState.FAILED,
    AIModelTrainingRun.STATUS_LOST: OperationLifecycleState.LOST,
}

_MODEL_TRAINING_EVENTS: dict[
    tuple[OperationLifecycleState, OperationLifecycleState],
    tuple[OperationLifecycleEvent, ...],
] = {
    (OperationLifecycleState.QUEUED, OperationLifecycleState.RUNNING): (
        OperationLifecycleEvent.CLAIM,
        OperationLifecycleEvent.START,
    ),
    (OperationLifecycleState.QUEUED, OperationLifecycleState.RETRY_WAIT): (
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ),
    (OperationLifecycleState.RETRY_WAIT, OperationLifecycleState.RUNNING): (
        OperationLifecycleEvent.RETRY_READY,
        OperationLifecycleEvent.CLAIM,
        OperationLifecycleEvent.START,
    ),
    (OperationLifecycleState.RETRY_WAIT, OperationLifecycleState.QUEUED): (
        OperationLifecycleEvent.RETRY_READY,
    ),
    (OperationLifecycleState.RUNNING, OperationLifecycleState.SUCCEEDED): (
        OperationLifecycleEvent.SUCCEED,
    ),
    (OperationLifecycleState.RUNNING, OperationLifecycleState.FAILED): (
        OperationLifecycleEvent.FAIL,
    ),
    (OperationLifecycleState.QUEUED, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ),
    (OperationLifecycleState.RUNNING, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ),
    (OperationLifecycleState.SUCCEEDED, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.INTEGRITY_LOST,
    ),
    (OperationLifecycleState.LOST, OperationLifecycleState.RETRY_WAIT): (
        OperationLifecycleEvent.RECONCILE_RETRY,
    ),
    (OperationLifecycleState.RETRY_WAIT, OperationLifecycleState.FAILED): (
        OperationLifecycleEvent.FAIL,
    ),
    (OperationLifecycleState.QUEUED, OperationLifecycleState.FAILED): (
        OperationLifecycleEvent.FAIL,
    ),
}

_MODEL_TRAINING_IDEMPOTENT_EVENTS: dict[
    OperationLifecycleState,
    OperationLifecycleEvent,
] = {
    OperationLifecycleState.QUEUED: OperationLifecycleEvent.RETRY_READY,
    OperationLifecycleState.FAILED: OperationLifecycleEvent.FAIL,
    OperationLifecycleState.RETRY_WAIT: OperationLifecycleEvent.RETRY_SCHEDULED,
    OperationLifecycleState.LOST: OperationLifecycleEvent.OWNERSHIP_LOST,
}


def _validate_model_training_status_transition(
    *,
    current_status: str,
    target_status: str,
) -> None:
    try:
        current_state = _MODEL_TRAINING_STATES[current_status]
        target_state = _MODEL_TRAINING_STATES[target_status]
    except KeyError as exc:
        raise ValueError(f"unknown model-training status: {exc.args[0]}") from exc

    if current_state is target_state:
        try:
            events = (_MODEL_TRAINING_IDEMPOTENT_EVENTS[current_state],)
        except KeyError as exc:
            raise ValueError(
                "non-idempotent model-training status transition: "
                f"{current_status} -> {target_status}"
            ) from exc
    else:
        try:
            events = _MODEL_TRAINING_EVENTS[(current_state, target_state)]
        except KeyError as exc:
            raise ValueError(
                "invalid model-training status transition: "
                f"{current_status} -> {target_status}"
            ) from exc

    reduced_state = reduce_operation_lifecycle(current_state, events)
    if reduced_state is not target_state:
        raise RuntimeError(
            "native model-training lifecycle reduction produced unexpected state: "
            f"{reduced_state.value}"
        )


def _transition_model_training_run(
    run_uuid: UUID,
    *,
    status: str,
    **updates: Any,
) -> int:
    current_status = (
        AIModelTrainingRun.objects.filter(run_id=run_uuid)
        .values_list(
            "status",
            flat=True,
        )
        .first()
    )
    if current_status is None:
        return 0
    _validate_model_training_status_transition(
        current_status=current_status,
        target_status=status,
    )
    normalized = _validated_model_training_run_updates({"status": status, **updates})
    updated = AIModelTrainingRun.objects.filter(
        run_id=run_uuid,
        status=current_status,
    ).update(**normalized)
    if updated != 1:
        raise RuntimeError(
            "model-training status changed concurrently before persistence: "
            f"run_id={run_uuid} expected_status={current_status}"
        )
    return updated


@dataclass(frozen=True)
class ModelTrainingFence:
    run_id: UUID
    attempt_id: UUID
    owner_id: str
    fencing_token: int


def _model_training_database_now(run_uuid: UUID) -> datetime:
    value = (
        AIModelTrainingRun.objects.filter(run_id=run_uuid)
        .annotate(database_now=Now())
        .values_list("database_now", flat=True)
        .get()
    )
    if not isinstance(value, datetime):
        raise RuntimeError("database did not return a training lease timestamp")
    return value


def _claim_model_training_run(
    run_uuid: UUID,
    *,
    lease_seconds: int = MODEL_TRAINING_LEASE_SECONDS,
) -> ModelTrainingFence | None:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    owner_id = uuid4().hex
    attempt_id = uuid4()
    with transaction.atomic():
        run = AIModelTrainingRun.objects.select_for_update().get(run_id=run_uuid)
        now = _model_training_database_now(run_uuid)
        if run.status == AIModelTrainingRun.STATUS_RUNNING:
            if run.lease_expires_at is not None and run.lease_expires_at > now:
                return None
            _validate_model_training_status_transition(
                current_status=run.status,
                target_status=AIModelTrainingRun.STATUS_LOST,
            )
            run.status = AIModelTrainingRun.STATUS_LOST
            run.attempt_id = None
            run.owner_id = ""
            run.heartbeat_at = None
            run.lease_expires_at = None
        if run.status == AIModelTrainingRun.STATUS_LOST:
            _validate_model_training_status_transition(
                current_status=run.status,
                target_status=AIModelTrainingRun.STATUS_RETRY_WAIT,
            )
            run.status = AIModelTrainingRun.STATUS_RETRY_WAIT
        _validate_model_training_status_transition(
            current_status=run.status,
            target_status=AIModelTrainingRun.STATUS_RUNNING,
        )
        run.status = AIModelTrainingRun.STATUS_RUNNING
        run.attempt_id = attempt_id
        run.owner_id = owner_id
        run.fencing_token += 1
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.started_at = now
        run.finished_at = None
        run.next_retry_at = None
        run.dispatch_error = ""
        run.server_instance_id = MODEL_TRAINING_SERVER_INSTANCE_ID
        run.save(
            update_fields=[
                "status",
                "attempt_id",
                "owner_id",
                "fencing_token",
                "heartbeat_at",
                "lease_expires_at",
                "started_at",
                "finished_at",
                "next_retry_at",
                "dispatch_error",
                "server_instance_id",
                "updated_at",
            ]
        )
        return ModelTrainingFence(
            run_id=run_uuid,
            attempt_id=attempt_id,
            owner_id=owner_id,
            fencing_token=int(run.fencing_token),
        )


def _renew_model_training_fence(
    fence: ModelTrainingFence,
    *,
    lease_seconds: int = MODEL_TRAINING_LEASE_SECONDS,
) -> None:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    with transaction.atomic():
        run = AIModelTrainingRun.objects.select_for_update().get(run_id=fence.run_id)
        now = _model_training_database_now(fence.run_id)
        if (
            run.status != AIModelTrainingRun.STATUS_RUNNING
            or run.attempt_id != fence.attempt_id
            or run.owner_id != fence.owner_id
            or int(run.fencing_token) != fence.fencing_token
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            raise RuntimeError("model-training ownership fence is no longer current")
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.save(update_fields=["heartbeat_at", "lease_expires_at", "updated_at"])


def _finish_model_training_run(
    fence: ModelTrainingFence,
    *,
    status: str,
    **updates: Any,
) -> None:
    _validate_model_training_status_transition(
        current_status=AIModelTrainingRun.STATUS_RUNNING,
        target_status=status,
    )
    normalized = _validated_model_training_run_updates(
        {
            "status": status,
            "attempt_id": None,
            "owner_id": "",
            "heartbeat_at": None,
            "lease_expires_at": None,
            **updates,
        }
    )
    updated = AIModelTrainingRun.objects.filter(
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        owner_id=fence.owner_id,
        fencing_token=fence.fencing_token,
        status=AIModelTrainingRun.STATUS_RUNNING,
        lease_expires_at__gt=Now(),
    ).update(**normalized)
    if updated != 1:
        raise RuntimeError("model-training completion rejected by ownership fence")


class ModelTrainingFenceHeartbeat:
    def __init__(self, fence: ModelTrainingFence) -> None:
        self._fence = fence
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "ModelTrainingFenceHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=MODEL_TRAINING_HEARTBEAT_SECONDS + 5)
        if self._failure is not None:
            raise RuntimeError("model-training heartbeat failed") from self._failure

    def _run(self) -> None:
        close_old_connections()
        try:
            while not self._stop.wait(MODEL_TRAINING_HEARTBEAT_SECONDS):
                _renew_model_training_fence(self._fence)
        except BaseException as exc:
            self._failure = exc
            self._stop.set()
        finally:
            close_old_connections()


class _TrainingResult(TypedDict, total=False):
    artifacts: list[object]


def _coerce_uuid(value: str) -> UUID | None:
    try:
        parsed = UUID(str(value))
        if parsed.version != 4:
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _parse_model_training_result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)
    return None


def _model_training_artifact_paths(result: dict[str, Any] | None) -> dict[str, str]:
    if not result:
        return {}
    paths: dict[str, str] = {}
    for key in (
        "model_path",
        "manifest_path",
        "meta_path",
        "training_result_path",
        "checkpoint_path",
        "onnx_path",
    ):
        value = result.get(key)
        if isinstance(value, str) and value:
            paths[key] = value
    training_result = result.get("training_result")
    if isinstance(training_result, dict):
        training_result_typed = cast(_TrainingResult, training_result)
        artifacts = training_result_typed.get("artifacts")
        if artifacts is not None:
            for artifact in artifacts:
                if not isinstance(artifact, Mapping):
                    continue
                artifact_map = cast(Mapping[str, object], artifact)
                raw_kind = artifact_map.get("kind")
                kind = str(raw_kind or "").strip().lower()
                path = artifact_map.get("path")
                if kind and isinstance(path, str) and path:
                    paths[f"{kind}_path"] = path
    return paths


def _require_model_training_artifacts(artifact_paths: Mapping[str, str]) -> None:
    if not artifact_paths:
        raise RuntimeError("Model training completed without an artifact manifest.")
    missing = sorted(
        path for path in artifact_paths.values() if not Path(path).is_file()
    )
    if missing:
        raise RuntimeError(
            "Model training artifact verification failed before publication: "
            f"missing_count={len(missing)}"
        )


def _model_training_staging_root() -> Path:
    return Path(
        getattr(
            settings,
            "MODEL_TRAINING_STAGING_ROOT",
            DEFAULT_MODEL_TRAINING_STAGING_ROOT,
        )
    )


def _create_run_staging_dir(run_id: str) -> Path:
    root = ensure_directory(_model_training_staging_root(), dir_mode=0o750)
    staging_dir = root / f"run-{run_id}-{uuid4().hex}"
    return ensure_directory(staging_dir, dir_mode=0o750)


def _consecutive_ranges(frame_numbers: list[int]) -> list[tuple[int, int]]:
    if not frame_numbers:
        return []
    ranges: list[tuple[int, int]] = []
    start = previous = frame_numbers[0]
    for frame_number in frame_numbers[1:]:
        if frame_number == previous + 1:
            previous = frame_number
            continue
        ranges.append((start, previous + 1))
        start = previous = frame_number
    ranges.append((start, previous + 1))
    return ranges


def _assert_processed_video_training_ready(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    missing_flags = [
        field_name
        for field_name in (
            "sensitive_meta_processed",
            "anonymized",
            "anonymization_validated",
            "outside_segments_removed",
        )
        if not bool(getattr(state, field_name, False))
    ]
    if missing_flags:
        raise RuntimeError(
            "Cannot materialize training frames from processed video "
            f"{video.video_hash}: missing readiness flags={missing_flags}."
        )
    if not bool(getattr(video, "is_processed", False)):
        raise FileNotFoundError(
            "Cannot materialize training frames from processed video "
            f"{video.video_hash}: processed file is not available."
        )


def _expected_frame_relative_path(frame_number: int, ext: str = "jpg") -> str:
    return f"frame_{frame_number:07d}.{ext}"


def _merge_frame_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]
    for start_frame, end_frame in intervals[1:]:
        previous_start, previous_end = merged[-1]
        if start_frame <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end_frame))
        else:
            merged.append((start_frame, end_frame))
    return merged


def _frame_interval_query(intervals: list[tuple[int, int]]) -> Any:
    from django.db.models import Q

    frame_query = Q()
    for start_frame, end_frame in intervals:
        frame_query |= Q(frame_number__gte=start_frame, frame_number__lt=end_frame)
    return frame_query


def _add_segment_training_frames(
    *,
    frames_by_video: dict[int, dict[int, Frame]],
    segments: list[LabelVideoSegment],
) -> None:
    segments_by_video: dict[int, list[LabelVideoSegment]] = defaultdict(list)
    for segment in segments:
        if segment.start_frame_number >= segment.end_frame_number:
            continue
        segment_video = segment.video_file
        segments_by_video[int(segment_video.pk)].append(segment)

    for video_id, video_segments in segments_by_video.items():
        intervals = _merge_frame_intervals(
            [
                (int(segment.start_frame_number), int(segment.end_frame_number))
                for segment in video_segments
            ]
        )
        if not intervals:
            continue

        frames_qs = Frame.objects.select_related("video").filter(video_id=video_id)
        if len(intervals) <= 120:
            frames_qs = frames_qs.filter(_frame_interval_query(intervals))
        else:
            frames_qs = frames_qs.filter(
                frame_number__gte=intervals[0][0],
                frame_number__lt=max(
                    end_frame for _start_frame, end_frame in intervals
                ),
            )

        frame_by_number = frames_by_video.setdefault(int(video_id), {})
        for frame in frames_qs.order_by("frame_number", "pk"):
            frame_number = int(frame.frame_number)
            if any(
                start_frame <= frame_number < end_frame
                for start_frame, end_frame in intervals
            ):
                frame_by_number[frame_number] = frame


def _materialize_missing_multilabel_frames(
    dataset_id: int,
    *,
    annotation_source_scope: str | None = ANNOTATION_SOURCE_SCOPE_ALL,
    attempt_staging_dir: Path | None = None,
) -> dict[str, Any]:
    dataset = AIDataSet.objects.get(id=dataset_id)
    source_scope: AnnotationSourceScope = normalize_annotation_source_scope(
        annotation_source_scope
    )
    if dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
        raise ValueError("Training frame materialization requires an image AIDataSet.")
    if dataset.ai_model_type != AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL:
        raise ValueError(
            "Training frame materialization requires an image_multilabel_classification AIDataSet."
        )

    frames_by_video: dict[int, dict[int, Frame]] = defaultdict(dict)

    if uses_frame_annotations(source_scope):
        annotations = (
            dataset.image_annotations.select_related("frame__video")
            .filter(frame__isnull=False)
            .order_by("frame__video_id", "frame__frame_number", "frame_id")
        )
        for annotation in annotations:
            frame = annotation.frame
            frame_video = frame.video
            frames_by_video[int(frame_video.pk)][frame.frame_number] = frame

    if uses_segment_annotations(source_scope):
        video_segments = list(
            dataset.video_annotations.select_related("video_file", "label")
            .filter(
                label__isnull=False,
                video_file_id__isnull=False,
                start_frame_number__isnull=False,
                end_frame_number__isnull=False,
            )
            .order_by("video_file_id", "start_frame_number", "end_frame_number", "pk")
        )
        _add_segment_training_frames(
            frames_by_video=frames_by_video,
            segments=video_segments,
        )

    materialized_count = 0
    existing_count = 0
    video_count = 0
    for frame_by_number in frames_by_video.values():
        if not frame_by_number:
            continue
        missing_numbers: list[int] = []
        sample_frame = next(iter(frame_by_number.values()))
        video = sample_frame.video
        for frame_number, frame in sorted(frame_by_number.items()):
            if frame.file_path.is_file():
                existing_count += 1
                if not frame.is_extracted:
                    frame.is_extracted = True
                    frame.save(update_fields=["is_extracted"])
                continue
            missing_numbers.append(frame_number)

        if not missing_numbers:
            continue

        video_count += 1
        _assert_processed_video_training_ready(video)
        frame_dir = get_video_frame_dir_path(video)
        if frame_dir is None:
            raise ValueError(
                f"Cannot determine frame directory path for video {video.video_hash}."
            )
        ensure_directory(frame_dir, dir_mode=0o750)
        extraction_dir = frame_dir
        if attempt_staging_dir is not None:
            extraction_dir = ensure_directory(
                attempt_staging_dir / f"video-{video.pk}",
                dir_mode=0o750,
            )

        for start_frame, end_frame in _consecutive_ranges(missing_numbers):
            extract_frame_range_to_directory(
                video,
                output_dir=extraction_dir,
                start_frame=start_frame,
                end_frame=end_frame,
                ext="jpg",
                from_processed=True,
            )

        verified_candidates: list[tuple[Frame, str, Path, Path]] = []
        for frame_number in missing_numbers:
            frame = frame_by_number[frame_number]
            expected_relative_path = _expected_frame_relative_path(frame_number)
            extracted_path = extraction_dir / expected_relative_path
            if not extracted_path.is_file():
                raise RuntimeError(
                    "Processed-video frame extraction did not create required "
                    f"training frame {frame_number} for video {video.video_hash}."
                )
            expected_path = frame_dir / expected_relative_path
            verified_candidates.append(
                (frame, expected_relative_path, extracted_path, expected_path)
            )

        verified_frames: list[tuple[Frame, str]] = []
        for (
            frame,
            expected_relative_path,
            extracted_path,
            expected_path,
        ) in verified_candidates:
            if extracted_path != expected_path:
                atomic_move_file(source=extracted_path, destination=expected_path)
            if not expected_path.is_file():
                raise RuntimeError(
                    "Verified training frame was not published to its canonical path: "
                    f"{expected_relative_path}."
                )
            verified_frames.append((frame, expected_relative_path))

        if verified_frames:
            with transaction.atomic():
                for frame, expected_relative_path in verified_frames:
                    frame.relative_path = expected_relative_path
                    frame.is_extracted = True
                    frame.save(update_fields=["relative_path", "is_extracted"])
            materialized_count += len(verified_frames)

    return {
        "dataset_id": dataset_id,
        "annotation_source_scope": source_scope,
        "existing_frame_count": existing_count,
        "materialized_frame_count": materialized_count,
        "materialized_video_count": video_count,
    }


def prepare_model_training_inputs(
    command_kwargs: dict[str, Any],
    *,
    attempt_staging_dir: Path | None = None,
) -> dict[str, Any]:
    command_name = str(
        command_kwargs.get("_command_name") or "train_image_multilabel_model"
    )
    if command_name != "train_image_multilabel_model":
        return {"prepared": False, "reason": "not_image_multilabel"}

    dataset_id = command_kwargs.get("dataset_id")
    if dataset_id is None:
        return {"prepared": False, "reason": "missing_dataset_id"}
    annotation_source_scope: AnnotationSourceScope = normalize_annotation_source_scope(
        command_kwargs.get("annotation_source_scope")
    )
    return {
        "prepared": True,
        **_materialize_missing_multilabel_frames(
            int(dataset_id),
            annotation_source_scope=annotation_source_scope,
            attempt_staging_dir=attempt_staging_dir,
        ),
    }


def _mark_lost_model_training_runs() -> None:
    now = timezone.now()
    stale_before = now - MODEL_TRAINING_DISPATCH_TIMEOUT
    candidate_ids = list(
        AIModelTrainingRun.objects.filter(
            status__in=[
                AIModelTrainingRun.STATUS_QUEUED,
                AIModelTrainingRun.STATUS_RUNNING,
                AIModelTrainingRun.STATUS_LOST,
            ]
        )
        .filter(
            Q(status=AIModelTrainingRun.STATUS_LOST)
            | Q(updated_at__lt=stale_before)
            | Q(lease_expires_at__lte=now)
        )
        .values_list("run_id", flat=True)
    )
    for run_uuid in candidate_ids:
        with transaction.atomic():
            run = AIModelTrainingRun.objects.select_for_update().get(run_id=run_uuid)
            database_now = _model_training_database_now(run_uuid)
            if run.status == AIModelTrainingRun.STATUS_RUNNING:
                if (
                    run.lease_expires_at is not None
                    and run.lease_expires_at > database_now
                ):
                    continue
                _validate_model_training_status_transition(
                    current_status=run.status,
                    target_status=AIModelTrainingRun.STATUS_LOST,
                )
                run.status = AIModelTrainingRun.STATUS_LOST
                run.attempt_id = None
                run.owner_id = ""
                run.heartbeat_at = None
                run.lease_expires_at = None
                run.finished_at = database_now
                run.error = "Training ownership lease expired before completion."
                run.save(
                    update_fields=[
                        "status",
                        "attempt_id",
                        "owner_id",
                        "heartbeat_at",
                        "lease_expires_at",
                        "finished_at",
                        "error",
                        "updated_at",
                    ]
                )
                continue
            if run.status == AIModelTrainingRun.STATUS_LOST:
                _validate_model_training_status_transition(
                    current_status=run.status,
                    target_status=AIModelTrainingRun.STATUS_RETRY_WAIT,
                )
                run.status = AIModelTrainingRun.STATUS_RETRY_WAIT
                run.next_retry_at = database_now
                run.save(update_fields=["status", "next_retry_at", "updated_at"])
                continue
            if run.status == AIModelTrainingRun.STATUS_QUEUED:
                _schedule_model_training_dispatch_retry(
                    run_uuid,
                    error="Training dispatch was not acknowledged before timeout.",
                )


def _model_training_run_payload(run: AIModelTrainingRun) -> dict[str, Any]:
    request_payload = cast(dict[str, object], run.request_payload or {})
    command_kwargs = cast(dict[str, object], run.command_kwargs or {})
    training_target = cast(
        str | None,
        request_payload.get("training_target"),
    )
    if training_target not in {
        MODEL_TRAINING_TARGET_IMAGE_MULTILABEL,
        MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
    }:
        training_target = (
            MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR
            if run.ai_model_type == MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR
            else MODEL_TRAINING_TARGET_IMAGE_MULTILABEL
        )

    annotation_source_scope = None
    if training_target == MODEL_TRAINING_TARGET_IMAGE_MULTILABEL:
        annotation_source_scope_raw = request_payload.get("annotation_source_scope")
        if not isinstance(annotation_source_scope_raw, str):
            annotation_source_scope_raw = command_kwargs.get("annotation_source_scope")
            if not isinstance(annotation_source_scope_raw, str):
                annotation_source_scope_raw = None
        annotation_source_scope = normalize_annotation_source_scope(
            annotation_source_scope_raw
        )

    dataset = run.dataset
    dataset_id = dataset.pk if dataset is not None else None

    return {
        "run_id": run.run_key,
        "training_target": training_target,
        "annotation_source_scope": annotation_source_scope,
        "status": run.status,
        "dataset_id": dataset_id,
        "dataset_name": run.dataset_name,
        "dataset_type": run.dataset_type,
        "ai_model_type": run.ai_model_type,
        "backbone_name": run.backbone_name,
        "feature_mode": run.feature_mode,
        "freeze_backbone": run.freeze_backbone,
        "epochs": run.epochs,
        "batch_size": run.batch_size,
        "labelset_version": run.labelset_version,
        "treat_unlabeled_as_negative": run.treat_unlabeled_as_negative,
        "backbone_checkpoint": run.backbone_checkpoint,
        "created_at": _isoformat(run.created_at),
        "started_at": _isoformat(run.started_at),
        "finished_at": _isoformat(run.finished_at),
        "result": run.result,
        "artifact_paths": run.artifact_paths,
        "error": run.error or None,
        "stdout": run.stdout,
        "stderr": run.stderr,
    }


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _execute_model_training_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
    raise_on_error: bool = False,
) -> None:
    run_uuid = _coerce_uuid(run_id)
    if run_uuid is None:
        return

    fence = _claim_model_training_run(run_uuid)
    if fence is None:
        return
    staging_dir: Path | None = None
    stdout = StringIO()
    stderr = StringIO()
    try:
        with ModelTrainingFenceHeartbeat(fence):
            staging_dir = _create_run_staging_dir(run_id)
            preparation = prepare_model_training_inputs(
                command_kwargs,
                attempt_staging_dir=staging_dir,
            )
            stdout.write(
                f"[TRAINING_JOB] input_preparation={json.dumps(preparation)}\n"
            )
            command_name = str(
                command_kwargs.get("_command_name") or "train_image_multilabel_model"
            )
            command_options = {
                key: value
                for key, value in command_kwargs.items()
                if not key.startswith("_")
            }
            call_command(
                command_name,
                stdout=stdout,
                stderr=stderr,
                **command_options,
            )
            output = stdout.getvalue()
            error_output = stderr.getvalue()
            result = _parse_model_training_result(output)
            validated_result = validate_ai_model_training_result_payload(result)
            artifact_paths = validate_ai_model_training_artifact_paths(
                _model_training_artifact_paths(validated_result)
            )
            _require_model_training_artifacts(artifact_paths)
            _finish_model_training_run(
                fence,
                status=AIModelTrainingRun.STATUS_COMPLETED,
                finished_at=timezone.now(),
                stdout=output,
                stderr=error_output,
                result=validated_result,
                artifact_paths=artifact_paths,
                error="",
            )
    except Exception as exc:
        output = stdout.getvalue()
        error_output = stderr.getvalue()
        trace = traceback.format_exc()
        combined_output = "\n".join(
            chunk for chunk in (output, error_output, trace) if chunk
        ).strip()
        try:
            _finish_model_training_run(
                fence,
                status=AIModelTrainingRun.STATUS_FAILED,
                finished_at=timezone.now(),
                stdout=combined_output,
                stderr=error_output,
                error=str(exc),
                result=None,
                artifact_paths={},
            )
        except RuntimeError:
            if raise_on_error:
                raise
        if raise_on_error:
            raise
    finally:
        if staging_dir is not None:
            safe_rmtree(staging_dir, missing_ok=True)


def _launch_model_training_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
) -> None:
    mode = str(getattr(settings, "MODEL_TRAINING_JOB_MODE", "celery")).lower()
    if mode == "celery":
        from endoreg_db.tasks import run_model_training_task

        ensure_secure_transport_for_job_kind(HeavyJobKind.MODEL_TRAINING)
        try:
            run_model_training_task.apply_async(
                args=(run_id, command_kwargs),
                queue=getattr(settings, "CELERY_TRAINING_QUEUE", "model_training"),
                routing_key=getattr(
                    settings, "CELERY_TRAINING_QUEUE", "model_training"
                ),
            )
        except Exception as exc:
            run_uuid = _coerce_uuid(run_id)
            if run_uuid is not None:
                _schedule_model_training_dispatch_retry(run_uuid, error=str(exc))
            raise
        return
    if mode == "inline":
        _execute_model_training_run(run_id, command_kwargs=command_kwargs)
        return
    if mode != "thread":
        mode = "thread"

    thread = threading.Thread(
        target=_execute_model_training_run,
        kwargs={"run_id": run_id, "command_kwargs": command_kwargs},
        daemon=True,
    )
    thread.start()


def _model_training_retry_delay(retry_count: int) -> int:
    exponent = max(0, min(int(retry_count), 10))
    return min(
        MODEL_TRAINING_RETRY_BASE_SECONDS * (2**exponent),
        MODEL_TRAINING_RETRY_MAX_SECONDS,
    )


def _schedule_model_training_dispatch_retry(run_uuid: UUID, *, error: str) -> None:
    with transaction.atomic():
        run = AIModelTrainingRun.objects.select_for_update().get(run_id=run_uuid)
        if run.status != AIModelTrainingRun.STATUS_QUEUED:
            return
        now = _model_training_database_now(run_uuid)
        if run.retry_count >= run.max_retries:
            _validate_model_training_status_transition(
                current_status=run.status,
                target_status=AIModelTrainingRun.STATUS_FAILED,
            )
            run.status = AIModelTrainingRun.STATUS_FAILED
            run.finished_at = now
            run.next_retry_at = None
        else:
            _validate_model_training_status_transition(
                current_status=run.status,
                target_status=AIModelTrainingRun.STATUS_RETRY_WAIT,
            )
            run.retry_count += 1
            run.status = AIModelTrainingRun.STATUS_RETRY_WAIT
            run.next_retry_at = now + timedelta(
                seconds=_model_training_retry_delay(run.retry_count - 1)
            )
        run.dispatch_error = error
        run.save(
            update_fields=[
                "status",
                "retry_count",
                "next_retry_at",
                "dispatch_error",
                "finished_at",
                "updated_at",
            ]
        )


def dispatch_due_model_training_retries(*, limit: int = 25) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    dispatched = 0
    now = timezone.now()
    due_ids = list(
        AIModelTrainingRun.objects.filter(
            status=AIModelTrainingRun.STATUS_RETRY_WAIT,
            next_retry_at__lte=now,
        )
        .order_by("next_retry_at")
        .values_list("run_id", flat=True)[:limit]
    )
    for run_uuid in due_ids:
        with transaction.atomic():
            run = AIModelTrainingRun.objects.select_for_update().get(run_id=run_uuid)
            if (
                run.status != AIModelTrainingRun.STATUS_RETRY_WAIT
                or run.next_retry_at is None
                or run.next_retry_at > timezone.now()
            ):
                continue
            _validate_model_training_status_transition(
                current_status=run.status,
                target_status=AIModelTrainingRun.STATUS_QUEUED,
            )
            run.status = AIModelTrainingRun.STATUS_QUEUED
            run.next_retry_at = None
            run.save(update_fields=["status", "next_retry_at", "updated_at"])
            command_kwargs = cast(dict[str, Any], run.command_kwargs)
        try:
            _launch_model_training_run(run.run_key, command_kwargs=command_kwargs)
        except Exception:
            continue
        dispatched += 1
    return dispatched


def reconcile_model_training_artifacts(*, limit: int = 100) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    lost = 0
    runs = AIModelTrainingRun.objects.filter(
        status=AIModelTrainingRun.STATUS_COMPLETED
    ).order_by("updated_at")[:limit]
    for run in runs:
        artifact_paths = validate_ai_model_training_artifact_paths(run.artifact_paths)
        if artifact_paths and all(
            Path(path).is_file() for path in artifact_paths.values()
        ):
            continue
        _validate_model_training_status_transition(
            current_status=run.status,
            target_status=AIModelTrainingRun.STATUS_LOST,
        )
        updated = AIModelTrainingRun.objects.filter(
            pk=run.pk,
            status=AIModelTrainingRun.STATUS_COMPLETED,
        ).update(
            status=AIModelTrainingRun.STATUS_LOST,
            error="Confirmed model-training artifact loss after successful publication.",
            finished_at=Now(),
            updated_at=Now(),
        )
        lost += int(updated == 1)
    return lost
