# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import json
import threading
import traceback
from collections import defaultdict
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management import call_command
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
from endoreg_db.services.video_files._frames._manage_frame_range import (
    extract_frame_range_to_directory,
)
from endoreg_db.schemas import (
    validate_ai_model_training_artifact_paths,
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
from endoreg_db.utils.file_operations import ensure_directory, safe_rmtree

MODEL_TRAINING_TARGET_IMAGE_MULTILABEL = "image_multilabel"
MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR = "phi_region_detector"
MODEL_TRAINING_SERVER_INSTANCE_ID = uuid4().hex
MODEL_TRAINING_LOST_TIMEOUT = timedelta(hours=25)
DEFAULT_MODEL_TRAINING_STAGING_ROOT = Path("/mnt/fast-nvme-cache/endoreg-training")


class _TrainingArtifact(TypedDict, total=False):
    kind: str
    path: str


class _TrainingResult(TypedDict, total=False):
    artifacts: list[_TrainingArtifact]


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
                kind = str(artifact.get("kind") or "").strip().lower()
                path = artifact.get("path")
                if kind and isinstance(path, str) and path:
                    paths[f"{kind}_path"] = path
    return paths


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

        for start_frame, end_frame in _consecutive_ranges(missing_numbers):
            extract_frame_range_to_directory(
                video,
                output_dir=frame_dir,
                start_frame=start_frame,
                end_frame=end_frame,
                ext="jpg",
                from_processed=True,
            )

        materialized_frame_ids: list[int] = []
        for frame_number in missing_numbers:
            frame = frame_by_number[frame_number]
            expected_relative_path = _expected_frame_relative_path(frame_number)
            update_fields: list[str] = []
            if frame.relative_path != expected_relative_path:
                frame.relative_path = expected_relative_path
                update_fields.append("relative_path")
            if not frame.is_extracted:
                frame.is_extracted = True
                update_fields.append("is_extracted")
            if update_fields:
                frame.save(update_fields=update_fields)
            if not frame.file_path.is_file():
                raise RuntimeError(
                    "Processed-video frame extraction did not create required "
                    f"training frame {frame_number} for video {video.video_hash}."
                )
            materialized_frame_ids.append(frame.pk)

        if materialized_frame_ids:
            Frame.objects.filter(pk__in=materialized_frame_ids).update(
                is_extracted=True
            )
            materialized_count += len(materialized_frame_ids)

    return {
        "dataset_id": dataset_id,
        "annotation_source_scope": source_scope,
        "existing_frame_count": existing_count,
        "materialized_frame_count": materialized_count,
        "materialized_video_count": video_count,
    }


def prepare_model_training_inputs(command_kwargs: dict[str, Any]) -> dict[str, Any]:
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
        ),
    }


def _mark_lost_model_training_runs() -> None:
    now = timezone.now()
    stale_before = now - MODEL_TRAINING_LOST_TIMEOUT
    AIModelTrainingRun.objects.filter(
        status__in=[
            AIModelTrainingRun.STATUS_QUEUED,
            AIModelTrainingRun.STATUS_RUNNING,
        ],
        updated_at__lt=stale_before,
    ).exclude(server_instance_id=MODEL_TRAINING_SERVER_INSTANCE_ID).update(
        status=AIModelTrainingRun.STATUS_LOST,
        finished_at=now,
        error=(
            "Training run remained queued/running without an update after "
            "backend process ownership changed. Marked LOST so the result is "
            "not silently hidden."
        ),
    )


def _model_training_run_payload(run: AIModelTrainingRun) -> dict[str, Any]:
    request_payload = run.request_payload or {}
    command_kwargs = run.command_kwargs or {}
    training_target = request_payload.get("training_target")
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
        annotation_source_scope = normalize_annotation_source_scope(
            cast(
                str | None,
                request_payload.get("annotation_source_scope")
                or command_kwargs.get("annotation_source_scope"),
            )
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

    AIModelTrainingRun.objects.filter(run_id=run_uuid).update(
        status=AIModelTrainingRun.STATUS_RUNNING,
        started_at=timezone.now(),
        server_instance_id=MODEL_TRAINING_SERVER_INSTANCE_ID,
    )
    staging_dir: Path | None = None
    stdout = StringIO()
    stderr = StringIO()
    try:
        staging_dir = _create_run_staging_dir(run_id)
        preparation = prepare_model_training_inputs(command_kwargs)
        stdout.write(f"[TRAINING_JOB] input_preparation={json.dumps(preparation)}\n")
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
        AIModelTrainingRun.objects.filter(run_id=run_uuid).update(
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
        AIModelTrainingRun.objects.filter(run_id=run_uuid).update(
            status=AIModelTrainingRun.STATUS_FAILED,
            finished_at=timezone.now(),
            stdout=combined_output,
            stderr=error_output,
            error=str(exc),
            result=None,
            artifact_paths={},
        )
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
        run_model_training_task.apply_async(
            args=(run_id, command_kwargs),
            queue=getattr(settings, "CELERY_TRAINING_QUEUE", "model_training"),
            routing_key=getattr(settings, "CELERY_TRAINING_QUEUE", "model_training"),
        )
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
