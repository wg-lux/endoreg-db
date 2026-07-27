from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Any, Protocol, cast

from django.core.management.base import CommandError, CommandParser
from django.db.models import Q, QuerySet

from endoreg_db.config.env import get_protected_media_url, nginx_offload_enabled
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files import VideoArtifactKind
from endoreg_db.services.hls_media import (
    coerce_hls_artifact_kind,
    mark_hls_materialization_dispatch_failed,
    materialize_video_hls,
    reserve_hls_materialization_dispatch,
)
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.tasks import video_hls_materialization
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.encryption.encryption import load_master_key

from ._video_command_base import BaseVideoCommand


class _TaskDispatcher(Protocol):
    def apply_async(self, *args: Any, **kwargs: Any) -> Any: ...


_HLS_STATUS_VALUES = (
    VideoHlsArtifact.Status.QUEUED.value,
    VideoHlsArtifact.Status.MATERIALIZING.value,
    VideoHlsArtifact.Status.VALIDATED.value,
    VideoHlsArtifact.Status.READY.value,
    VideoHlsArtifact.Status.SUPERSEDED.value,
    VideoHlsArtifact.Status.FAILED.value,
)
_BOTH_ARTIFACT_KINDS = "both"


@dataclass(frozen=True)
class _PreflightResult:
    master_key_available: bool
    ffmpeg_available: bool
    ffmpeg_executable: str
    nginx_offload_enabled: bool
    nginx_protected_media_url: str
    queue: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "master_key_available": self.master_key_available,
            "ffmpeg_available": self.ffmpeg_available,
            "ffmpeg_executable": self.ffmpeg_executable,
            "nginx_offload_enabled": self.nginx_offload_enabled,
            "nginx_protected_media_url": self.nginx_protected_media_url,
            "queue": self.queue,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class Command(BaseVideoCommand):
    help = (
        "Materialize legacy encrypted video media into AES-128 encrypted HLS "
        "artifacts. Defaults to dry-run selection; use --apply to dispatch work "
        "to the ffmpeg_media Celery queue."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        self.add_video_selection_arguments(parser)
        parser.add_argument(
            "--artifact-kind",
            default=_BOTH_ARTIFACT_KINDS,
            help=(
                "Video artifact to materialize as encrypted HLS. "
                "Defaults to both required local artifacts; use raw for local "
                "clinical review only or processed for anonymized playback only."
            ),
        )
        self.add_apply_argument(
            parser,
            help_text=(
                "Dispatch or run HLS materialization. Without this flag, dry run."
            ),
        )
        parser.add_argument(
            "--inline",
            action="store_true",
            help="Run materialization synchronously instead of dispatching Celery.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate HLS artifacts even if a ready artifact exists.",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="Stop after the first inline materialization failure.",
        )
        self.add_json_output_argument(parser)

    def handle(self, *args: object, **options: object) -> None:
        limit = self.positive_limit_from_options(options)
        selected_video_ids = self.selected_video_ids_from_options(options)
        artifact_kind = (
            str(options.get("artifact_kind") or _BOTH_ARTIFACT_KINDS).strip().lower()
        )
        if artifact_kind == _BOTH_ARTIFACT_KINDS:
            artifact_kinds = (
                VideoArtifactKind.RAW.value,
                VideoArtifactKind.PROCESSED.value,
            )
        else:
            try:
                artifact_kinds = (coerce_hls_artifact_kind(artifact_kind).value,)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
        apply_changes = bool(options.get("apply"))
        inline = bool(options.get("inline"))
        force = bool(options.get("force"))
        fail_fast = bool(options.get("fail_fast"))
        json_output = bool(options.get("json_output"))

        selected_videos = list(
            self._queryset(
                artifact_kinds=artifact_kinds,
                video_ids=selected_video_ids,
                limit=limit,
            )
        )
        selected_artifact_count = sum(
            1
            for video in selected_videos
            for selected_kind in artifact_kinds
            if self._video_has_source(video, artifact_kind=selected_kind)
        )

        results: list[dict[str, Any]] = []
        queue = queue_for_job_kind(HeavyJobKind.VIDEO_HLS_MATERIALIZATION)
        if artifact_kind == _BOTH_ARTIFACT_KINDS:
            audit: dict[str, object] = {
                selected_kind: self._audit_summary(
                    artifact_kind=selected_kind,
                    video_ids=selected_video_ids,
                )
                for selected_kind in artifact_kinds
            }
        else:
            audit = self._audit_summary(
                artifact_kind=artifact_kinds[0],
                video_ids=selected_video_ids,
            )
        preflight = self._preflight(queue=queue)
        if apply_changes and not inline:
            try:
                ensure_secure_transport_for_job_kind(
                    HeavyJobKind.VIDEO_HLS_MATERIALIZATION
                )
            except RuntimeError as exc:
                raise CommandError(str(exc)) from exc

        if apply_changes and selected_videos and preflight.errors:
            payload = self._payload(
                apply_changes=apply_changes,
                inline=inline,
                artifact_kind=artifact_kind,
                selected=len(selected_videos),
                selected_artifacts=selected_artifact_count,
                audit=audit,
                preflight=preflight,
                results=results,
            )
            if json_output:
                self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            raise CommandError(
                "HLS materialization preflight failed: " + "; ".join(preflight.errors)
            )

        stop_after_failure = False
        for video in selected_videos:
            video_id = int(video.pk)
            for selected_kind in artifact_kinds:
                if not self._video_has_source(video, artifact_kind=selected_kind):
                    continue
                if not apply_changes:
                    result = {
                        "video_id": video_id,
                        "artifact_kind": selected_kind,
                        "status": "would_materialize",
                    }
                elif inline:
                    try:
                        materialized = materialize_video_hls(
                            video_id,
                            artifact_kind=selected_kind,
                            force=force,
                        )
                        result = materialized.as_dict()
                    except Exception as exc:
                        result = {
                            "video_id": video_id,
                            "artifact_kind": selected_kind,
                            "status": "failed",
                            "error": str(exc),
                        }
                        stop_after_failure = fail_fast
                else:
                    reservation = reserve_hls_materialization_dispatch(
                        video_id=video_id,
                        artifact_kind=selected_kind,
                        force=force,
                    )
                    if reservation.status != "queued":
                        result = {
                            "video_id": video_id,
                            "artifact_kind": selected_kind,
                            "status": reservation.status,
                        }
                        results.append(result)
                        if not json_output:
                            self._write_result(result)
                        continue
                    task_dispatcher = cast(_TaskDispatcher, video_hls_materialization)
                    try:
                        async_result = task_dispatcher.apply_async(
                            args=[video_id, selected_kind, force],
                            queue=queue,
                            routing_key=queue,
                        )
                    except Exception as exc:
                        mark_hls_materialization_dispatch_failed(
                            artifact_id=reservation.artifact_id,
                            error=f"Celery HLS dispatch failed: {exc}",
                        )
                        result = {
                            "video_id": video_id,
                            "artifact_kind": selected_kind,
                            "status": "failed",
                            "error": str(exc),
                        }
                    else:
                        result = {
                            "video_id": video_id,
                            "artifact_kind": selected_kind,
                            "status": "queued",
                            "task_id": str(async_result.id),
                            "queue": queue,
                        }
                results.append(result)
                if not json_output:
                    self._write_result(result)
                if stop_after_failure:
                    break
            if stop_after_failure:
                break

        payload = self._payload(
            apply_changes=apply_changes,
            inline=inline,
            artifact_kind=artifact_kind,
            selected=len(selected_videos),
            selected_artifacts=selected_artifact_count,
            audit=audit,
            preflight=preflight,
            results=results,
        )
        if json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        elif not results:
            self.stdout.write("No matching videos selected.")

        failures = [result for result in results if result.get("status") == "failed"]
        if failures:
            raise CommandError(
                f"HLS materialization failed for {len(failures)} artifact(s)."
            )

    @staticmethod
    def _eligible_queryset(*, artifact_kind: str) -> QuerySet[VideoFile]:
        try:
            parsed_kind = coerce_hls_artifact_kind(artifact_kind)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        queryset = VideoFile.objects.all()
        source_field = (
            "raw_file" if parsed_kind == VideoArtifactKind.RAW else "processed_file"
        )
        return (
            queryset.exclude(**{source_field: ""})
            .exclude(**{f"{source_field}__isnull": True})
            .order_by("pk")
        )

    @staticmethod
    def _video_has_source(video: VideoFile, *, artifact_kind: str) -> bool:
        parsed_kind = coerce_hls_artifact_kind(artifact_kind)
        source = (
            video.raw_file
            if parsed_kind == VideoArtifactKind.RAW
            else video.processed_file
        )
        return bool(getattr(source, "name", ""))

    @staticmethod
    def _queryset(
        *,
        artifact_kinds: tuple[str, ...],
        video_ids: list[int] | None,
        limit: int | None,
    ) -> QuerySet[VideoFile]:
        source_filter = Q()
        for artifact_kind in artifact_kinds:
            parsed_kind = coerce_hls_artifact_kind(artifact_kind)
            source_field = (
                "raw_file" if parsed_kind == VideoArtifactKind.RAW else "processed_file"
            )
            source_filter |= Q(**{f"{source_field}__isnull": False}) & ~Q(
                **{source_field: ""}
            )
        queryset = VideoFile.objects.filter(source_filter).order_by("pk")
        return Command.apply_video_selection(
            queryset,
            video_ids=video_ids,
            limit=limit,
        )

    @staticmethod
    def _audit_summary(
        *,
        artifact_kind: str,
        video_ids: list[int] | None,
    ) -> dict[str, object]:
        all_videos = VideoFile.objects.all()
        if video_ids:
            all_videos = all_videos.filter(pk__in=video_ids)

        eligible_videos = Command._eligible_queryset(artifact_kind=artifact_kind)
        if video_ids:
            eligible_videos = eligible_videos.filter(pk__in=video_ids)

        hls_artifacts = VideoHlsArtifact.objects.filter(
            artifact_kind=artifact_kind,
            video_id__in=eligible_videos.values("pk"),
        )
        status_counter = Counter(
            str(status) for status in hls_artifacts.values_list("status", flat=True)
        )
        status_counts = {
            status: int(status_counter.get(status, 0)) for status in _HLS_STATUS_VALUES
        }
        total_count = all_videos.count()
        eligible_count = eligible_videos.count()
        hls_artifact_count = hls_artifacts.count()

        return {
            "total_videos": total_count,
            f"eligible_{artifact_kind}_videos": eligible_count,
            f"videos_without_{artifact_kind}_file": total_count - eligible_count,
            "hls_artifacts": status_counts,
            "missing_hls_artifacts": max(eligible_count - hls_artifact_count, 0),
        }

    @staticmethod
    def _preflight(*, queue: str) -> _PreflightResult:
        errors: list[str] = []
        warnings: list[str] = []

        try:
            load_master_key()
            master_key_available = True
        except (OSError, RuntimeError) as exc:
            master_key_available = False
            errors.append(str(exc))

        ffmpeg_executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
        if ffmpeg_executable is None:
            ffmpeg_path = ""
            ffmpeg_available = False
            errors.append("ffmpeg executable is not available")
        else:
            ffmpeg_path = ffmpeg_executable
            ffmpeg_available = True

        nginx_enabled = nginx_offload_enabled()
        if not nginx_enabled:
            warnings.append(
                "SERVE_WITH_NGINX is not enabled; HLS segments fail closed until "
                "protected-media nginx offload is configured."
            )

        return _PreflightResult(
            master_key_available=master_key_available,
            ffmpeg_available=ffmpeg_available,
            ffmpeg_executable=ffmpeg_path,
            nginx_offload_enabled=nginx_enabled,
            nginx_protected_media_url=get_protected_media_url(),
            queue=queue,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _payload(
        *,
        apply_changes: bool,
        inline: bool,
        artifact_kind: str,
        selected: int,
        selected_artifacts: int,
        audit: dict[str, object],
        preflight: _PreflightResult,
        results: list[dict[str, Any]],
    ) -> dict[str, object]:
        return {
            "apply": apply_changes,
            "inline": inline,
            "artifact_kind": artifact_kind,
            "selected": selected,
            "selected_artifacts": selected_artifacts,
            "audit": audit,
            "preflight": preflight.as_dict(),
            "results": results,
        }

    def _write_result(self, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        line = (
            f"video={result.get('video_id')} "
            f"kind={result.get('artifact_kind')} status={status}"
        )
        if result.get("task_id"):
            line = f"{line} task_id={result['task_id']}"
        if result.get("error"):
            line = f"{line} error={result['error']}"

        if status == "failed":
            self.stderr.write(self.style.ERROR(line))
        elif status in {"queued", "materialized", "already_ready", "already_queued"}:
            self.stdout.write(self.style.SUCCESS(line))
        else:
            self.stdout.write(line)
