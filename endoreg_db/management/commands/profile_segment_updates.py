from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.management.commands._profiling import (
    add_profiling_arguments,
    command_profiling_config_from_options,
    positive_int_option,
    profiling_metadata,
    run_with_optional_profile,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services.video_segments_bulk_mutation import bulk_mutate_video_segments
from endoreg_db.views.video.correction import update_segments_after_frame_removal

type _Operation = Literal["bulk-mutation", "frame-removal", "both"]


@dataclass(frozen=True)
class _CommandOptions:
    video_id: int | None
    operation: _Operation
    segments: int
    create_count: int
    update_count: int
    delete_count: int
    frame_count: int
    fps: float
    removed_frame_step: int
    label_name: str
    information_source_name: str
    commit: bool
    json_output: bool

    @property
    def runs_bulk_mutation(self) -> bool:
        return self.operation in {"bulk-mutation", "both"}

    @property
    def runs_frame_removal(self) -> bool:
        return self.operation in {"frame-removal", "both"}


class Command(BaseCommand):
    help = (
        "Profile segment update hot paths: bulk segment mutation and frame-removal "
        "boundary reconciliation. Generated data is rolled back unless --commit is used."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            type=int,
            default=None,
            help=(
                "Existing VideoFile id to profile against. The command rolls back "
                "database changes for existing videos."
            ),
        )
        parser.add_argument(
            "--operation",
            choices=("bulk-mutation", "frame-removal", "both"),
            default="both",
            help="Segment update path to exercise.",
        )
        parser.add_argument(
            "--segments",
            type=int,
            default=200,
            help="Profiler seed segments to create before timing the selected path.",
        )
        parser.add_argument(
            "--create-count",
            type=int,
            default=50,
            help="Number of segments to create through the bulk mutation service.",
        )
        parser.add_argument(
            "--update-count",
            type=int,
            default=50,
            help="Number of profiler seed segments to update through the bulk service.",
        )
        parser.add_argument(
            "--delete-count",
            type=int,
            default=50,
            help="Number of profiler seed segments to delete through the bulk service.",
        )
        parser.add_argument(
            "--frame-count",
            type=int,
            default=10_000,
            help="Frame count for synthetic profiler videos.",
        )
        parser.add_argument(
            "--fps",
            type=float,
            default=DEFAULT_VIDEO_FPS,
            help="FPS for synthetic profiler videos.",
        )
        parser.add_argument(
            "--removed-frame-step",
            type=int,
            default=25,
            help="Remove every Nth frame for frame-removal segment reconciliation.",
        )
        parser.add_argument(
            "--label-name",
            default="profile_segment_updates",
            help="Label name used for profiler-generated segments.",
        )
        parser.add_argument(
            "--information-source-name",
            default="manual_annotation",
            help="InformationSource.name used for profiler-generated seed segments.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist synthetic profiler data. Not allowed with --video-id.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit machine-readable JSON.",
        )
        add_profiling_arguments(parser)

    def handle(self, *args: object, **options: object) -> None:
        _ = args
        command_options = _command_options_from_raw_options(options)
        profiling_config = command_profiling_config_from_options(options)
        min_required_frame_count = _minimum_required_frame_count(command_options)

        if command_options.commit and command_options.video_id is not None:
            raise CommandError(
                "--commit is only supported for synthetic profiler data. "
                "Omit --video-id or omit --commit."
            )

        with transaction.atomic():
            video, frame_count, fps, synthetic_video = _resolve_video(
                command_options,
                min_required_frame_count=min_required_frame_count,
            )
            label = _resolve_label(command_options.label_name)
            source = _resolve_information_source(
                command_options.information_source_name
            )
            seed_count = _seed_segment_count(command_options)
            seed_segments = _create_seed_segments(
                video=video,
                label=label,
                source=source,
                count=seed_count,
            )

            started_at = time.perf_counter()
            result = run_with_optional_profile(
                lambda: _run_profiled_segment_updates(
                    video=video,
                    label=label,
                    seed_segments=seed_segments,
                    options=command_options,
                    frame_count=frame_count,
                    fps=fps,
                ),
                config=profiling_config,
            )
            elapsed_seconds = time.perf_counter() - started_at

            payload: dict[str, object] = {
                "operation": command_options.operation,
                "committed": command_options.commit,
                "rolled_back": not command_options.commit,
                "synthetic_video": synthetic_video,
                "video_id": video.pk,
                "fps": fps,
                "frame_count": frame_count,
                "seed_segments": len(seed_segments),
                "elapsed_wall_seconds": round(elapsed_seconds, 6),
                **result,
                **profiling_metadata(profiling_config),
            }

            if not command_options.commit:
                transaction.set_rollback(True)

        self._write_payload(payload, json_output=command_options.json_output)

    def _write_payload(self, payload: dict[str, object], *, json_output: bool) -> None:
        if json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        rollback = "rolled_back" if payload["rolled_back"] else "committed"
        self.stdout.write(
            self.style.SUCCESS(
                "segment update profiling complete: "
                f"operation={payload['operation']} "
                f"video_id={payload['video_id']} "
                f"seed_segments={payload['seed_segments']} "
                f"elapsed_wall_seconds={payload['elapsed_wall_seconds']} "
                f"{rollback}"
            )
        )


def _run_profiled_segment_updates(
    *,
    video: VideoFile,
    label: Label,
    seed_segments: list[LabelVideoSegment],
    options: _CommandOptions,
    frame_count: int,
    fps: float,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if options.runs_bulk_mutation:
        payload["bulk_mutation"] = _run_bulk_mutation_profile(
            video=video,
            label=label,
            seed_segments=seed_segments,
            options=options,
            fps=fps,
        )

    if options.runs_frame_removal:
        removed_frames = list(range(0, frame_count, options.removed_frame_step))
        payload["frame_removal"] = {
            "removed_frame_count": len(removed_frames),
            "result": update_segments_after_frame_removal(video, removed_frames),
        }

    return payload


def _run_bulk_mutation_profile(
    *,
    video: VideoFile,
    label: Label,
    seed_segments: list[LabelVideoSegment],
    options: _CommandOptions,
    fps: float,
) -> dict[str, object]:
    label_id = _required_pk(label, "Label")
    update_targets = seed_segments[: options.update_count]
    delete_targets = seed_segments[
        options.update_count : options.update_count + options.delete_count
    ]
    create_start_index = len(seed_segments)
    update_start_index = create_start_index + options.create_count
    payload = {
        "defer_annotation_sync": True,
        "creates": [
            {
                "client_id": -(index + 1),
                "label_id": label_id,
                **_time_payload(create_start_index + index, fps),
            }
            for index in range(options.create_count)
        ],
        "updates": [
            {
                "id": _required_pk(segment, "LabelVideoSegment"),
                "label_id": label_id,
                **_time_payload(update_start_index + index, fps),
            }
            for index, segment in enumerate(update_targets)
        ],
        "deletes": [
            _required_pk(segment, "LabelVideoSegment") for segment in delete_targets
        ],
    }
    result = bulk_mutate_video_segments(video=video, payload=payload)
    return {
        "requested_create_count": options.create_count,
        "requested_update_count": len(update_targets),
        "requested_delete_count": len(delete_targets),
        "result": result,
    }


def _command_options_from_raw_options(options: dict[str, object]) -> _CommandOptions:
    operation = str(options.get("operation") or "both")
    if operation not in {"bulk-mutation", "frame-removal", "both"}:
        raise CommandError("--operation must be bulk-mutation, frame-removal, or both.")

    command_options = _CommandOptions(
        video_id=_optional_positive_int_option(options.get("video_id"), "--video-id"),
        operation=cast(_Operation, operation),
        segments=_non_negative_int_option(options.get("segments"), "--segments"),
        create_count=_non_negative_int_option(
            options.get("create_count"),
            "--create-count",
        ),
        update_count=_non_negative_int_option(
            options.get("update_count"),
            "--update-count",
        ),
        delete_count=_non_negative_int_option(
            options.get("delete_count"),
            "--delete-count",
        ),
        frame_count=positive_int_option(options.get("frame_count"), "--frame-count"),
        fps=_positive_float_option(options.get("fps"), "--fps"),
        removed_frame_step=positive_int_option(
            options.get("removed_frame_step"),
            "--removed-frame-step",
        ),
        label_name=_required_str(options.get("label_name"), "--label-name"),
        information_source_name=_required_str(
            options.get("information_source_name"),
            "--information-source-name",
        ),
        commit=bool(options.get("commit")),
        json_output=bool(options.get("json_output")),
    )
    if command_options.runs_bulk_mutation and (
        command_options.create_count
        + command_options.update_count
        + command_options.delete_count
        == 0
    ):
        raise CommandError(
            "Bulk mutation profiling requires at least one create, update, or delete."
        )
    return command_options


def _resolve_video(
    options: _CommandOptions,
    *,
    min_required_frame_count: int,
) -> tuple[VideoFile, int, float, bool]:
    if options.video_id is not None:
        video = VideoFile.objects.filter(pk=options.video_id).first()
        if video is None:
            raise CommandError(f"VideoFile not found: {options.video_id}")
        frame_count = int(video.frame_count or 0)
        fps = float(video.fps or 0.0)
        if frame_count < min_required_frame_count:
            raise CommandError(
                f"VideoFile {options.video_id} frame_count={frame_count} is too small "
                f"for this profile; need at least {min_required_frame_count}. "
                "Lower the requested counts or use synthetic profiler data."
            )
        if fps <= 0:
            raise CommandError(
                f"VideoFile {options.video_id} must have a positive fps value."
            )
        return video, frame_count, fps, False

    frame_count = max(options.frame_count, min_required_frame_count)
    center = Center.objects.create(
        name=f"profile-segment-updates-{uuid4().hex[:12]}",
        display_name="Segment Update Profiling",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"profile-segment-updates-{uuid4().hex}",
        original_file_name="profile_segment_updates.mp4",
        fps=options.fps,
        frame_count=frame_count,
    )
    return video, frame_count, options.fps, True


def _resolve_label(label_name: str) -> Label:
    label, _ = Label.objects.get_or_create(name=label_name)
    return label


def _resolve_information_source(source_name: str) -> InformationSource:
    source, _ = InformationSource.objects.get_or_create(name=source_name)
    return source


def _create_seed_segments(
    *,
    video: VideoFile,
    label: Label,
    source: InformationSource,
    count: int,
) -> list[LabelVideoSegment]:
    if count <= 0:
        return []

    segments = [
        LabelVideoSegment(
            video_file=video,
            label=label,
            source=source,
            start_frame_number=_frame_bounds(index)[0],
            end_frame_number=_frame_bounds(index)[1],
        )
        for index in range(count)
    ]
    return list(LabelVideoSegment.objects.bulk_create(segments, batch_size=1_000))


def _seed_segment_count(options: _CommandOptions) -> int:
    required_for_bulk = (
        options.update_count + options.delete_count if options.runs_bulk_mutation else 0
    )
    required_for_frame_removal = 1 if options.runs_frame_removal else 0
    return max(options.segments, required_for_bulk, required_for_frame_removal)


def _minimum_required_frame_count(options: _CommandOptions) -> int:
    seed_count = _seed_segment_count(options)
    extra_slots = (
        options.create_count + options.update_count if options.runs_bulk_mutation else 0
    )
    total_slots = max(1, seed_count + extra_slots)
    return _frame_bounds(total_slots - 1)[1]


def _frame_bounds(index: int) -> tuple[int, int]:
    start = index * 4
    return start, start + 2


def _time_payload(index: int, fps: float) -> dict[str, float]:
    start_frame, end_frame = _frame_bounds(index)
    return {
        "start_time": start_frame / fps,
        "end_time": end_frame / fps,
    }


def _required_pk(instance: object, label: str) -> int:
    pk = getattr(instance, "pk", None)
    if not isinstance(pk, int):
        raise CommandError(f"{label} must be saved before profiling.")
    return pk


def _required_str(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CommandError(f"{label} must not be empty.")
    return text


def _optional_positive_int_option(value: object, label: str) -> int | None:
    if value is None:
        return None
    return positive_int_option(value, label)


def _non_negative_int_option(value: object, label: str) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{label} must be a non-negative integer.") from exc
    if result < 0:
        raise CommandError(f"{label} must be a non-negative integer.")
    return result


def _positive_float_option(value: object, label: str) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{label} must be a positive number.") from exc
    if result <= 0:
        raise CommandError(f"{label} must be a positive number.")
    return result
