from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeGuard, cast
from urllib.parse import urlsplit

from django.core.files.base import File
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Q, QuerySet
from django.db.models.fields.files import FieldFile

from endoreg_db.config.env import (
    get_protected_media_root,
    get_protected_media_url,
    nginx_offload_enabled,
)
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.serializers.video.video_file import VideoFileSerializer
from endoreg_db.services.hls_media import (
    HLS_CONTENT_KEY_BYTES,
    hls_playlist_path,
    hls_segment_path,
    unwrap_hls_content_key,
)
from endoreg_db.utils.encryption.encryption import load_master_key
from endoreg_db.utils.nginx_accel import build_nginx_accel_response_for_path


_MAX_REPORTED_IDS = 20
_FAST_SAMPLE_RATIO = 0.05
_HLS_PLAYLIST_CONTENT_TYPE = "application/vnd.apple.mpegurl"


class _EncryptedStorageLike(Protocol):
    def open(self, name: str, mode: str = "rb") -> File[bytes]: ...

    def is_encrypted(self, name: str) -> bool: ...


@dataclass(frozen=True)
class _ReadinessIssue:
    block: str
    message: str
    video_id: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "block": self.block,
            "message": self.message,
        }
        if self.video_id is not None:
            payload["video_id"] = self.video_id
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class _ReadinessRequest:
    base_url: str = "https://readiness.local"

    def build_absolute_uri(self, location: str | None = None) -> str:
        if location is None:
            return self.base_url
        if location.startswith(("http://", "https://")):
            return location
        return f"{self.base_url.rstrip('/')}/{location.lstrip('/')}"


@dataclass(frozen=True)
class _CheckSummary:
    eligible_videos: int
    failed_videos: int
    deep_checked_artifacts: int
    storage_checked_files: int
    api_checked_videos: int
    issues: tuple[_ReadinessIssue, ...]

    @property
    def valid_videos(self) -> int:
        return max(self.eligible_videos - self.failed_videos, 0)

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible_videos": self.eligible_videos,
            "valid_videos": self.valid_videos,
            "failed_videos": self.failed_videos,
            "deep_checked_artifacts": self.deep_checked_artifacts,
            "storage_checked_files": self.storage_checked_files,
            "api_checked_videos": self.api_checked_videos,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _has_encrypted_storage_api(storage: object) -> TypeGuard[_EncryptedStorageLike]:
    return callable(getattr(storage, "open", None)) and callable(
        getattr(storage, "is_encrypted", None)
    )


def _field_file_has_name(field_file: object) -> bool:
    name = getattr(field_file, "name", None)
    return isinstance(name, str) and bool(name.strip())


def _eligible_video_queryset() -> QuerySet[VideoFile]:
    return (
        VideoFile.objects.exclude(processed_file="")
        .exclude(processed_file__isnull=True)
        .order_by("pk")
    )


def _sample_limit(total: int, *, fast: bool, sample_size: int | None) -> int:
    if total <= 0:
        return 0
    if sample_size is not None:
        return min(sample_size, total)
    if fast:
        return max(1, math.ceil(total * _FAST_SAMPLE_RATIO))
    return total


def _format_ids(ids: list[int]) -> str:
    visible = ids[:_MAX_REPORTED_IDS]
    suffix = "" if len(ids) <= _MAX_REPORTED_IDS else " ..."
    return ", ".join(str(video_id) for video_id in visible) + suffix


def _readable_file(path: Path) -> bool:
    with path.open("rb") as handle:
        handle.read(1)
    return True


class Command(BaseCommand):
    help = (
        "Validate the production HLS-only launch gate for encrypted video "
        "storage, Nginx offload, HLS artifacts, and API playback URLs."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--fast",
            action="store_true",
            help=(
                "Run filesystem, storage, and API deep checks on a deterministic "
                "5 percent sample instead of every eligible video."
            ),
        )
        parser.add_argument(
            "--sample-size",
            type=int,
            default=None,
            help=(
                "Override the deep-check sample size. By default the command scans "
                "100 percent, or 5 percent with --fast."
            ),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit a machine-readable readiness summary.",
        )

    def handle(self, *args: object, **options: object) -> None:
        fast = bool(options.get("fast"))
        json_output = bool(options.get("json_output"))
        raw_sample_size = options.get("sample_size")
        sample_size = self._coerce_sample_size(raw_sample_size)

        issues: list[_ReadinessIssue] = []
        failed_video_ids: set[int] = set()

        eligible_video_ids = list(
            _eligible_video_queryset().values_list("pk", flat=True)
        )
        eligible_count = len(eligible_video_ids)

        self._check_master_key(issues)
        storage_checked_files = self._check_encrypted_storage(
            issues,
            fast=fast,
            sample_size=sample_size,
        )
        self._check_nginx_offload(issues)
        self._check_hls_artifact_completeness(
            issues,
            eligible_video_ids=eligible_video_ids,
            failed_video_ids=failed_video_ids,
        )
        self._check_legacy_streamable_paths(issues, failed_video_ids=failed_video_ids)
        deep_checked_artifacts = self._check_hls_filesystem_state(
            issues,
            eligible_video_ids=eligible_video_ids,
            failed_video_ids=failed_video_ids,
            fast=fast,
            sample_size=sample_size,
        )
        api_checked_videos = self._check_api_payload_contract(
            issues,
            eligible_video_ids=eligible_video_ids,
            failed_video_ids=failed_video_ids,
            fast=fast,
            sample_size=sample_size,
        )

        summary = _CheckSummary(
            eligible_videos=eligible_count,
            failed_videos=len(failed_video_ids),
            deep_checked_artifacts=deep_checked_artifacts,
            storage_checked_files=storage_checked_files,
            api_checked_videos=api_checked_videos,
            issues=tuple(issues),
        )

        if json_output:
            self.stdout.write(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
        else:
            self._write_human_summary(summary)

        if issues:
            sys.exit(1)

    @staticmethod
    def _coerce_sample_size(value: object) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int):
            raise CommandError("--sample-size must be an integer")
        if value <= 0:
            raise CommandError("--sample-size must be greater than zero")
        return value

    def _check_master_key(self, issues: list[_ReadinessIssue]) -> None:
        try:
            load_master_key()
        except Exception as exc:
            issues.append(
                _ReadinessIssue(
                    block="crypto_storage",
                    message="Master key could not be loaded.",
                    detail=str(exc),
                )
            )
            self.stdout.write(self.style.ERROR("[crypto] master key unavailable"))
            return

        self.stdout.write(self.style.SUCCESS("[crypto] master key available"))

    def _check_encrypted_storage(
        self,
        issues: list[_ReadinessIssue],
        *,
        fast: bool,
        sample_size: int | None,
    ) -> int:
        checked = 0
        for field_name in ("raw_file", "processed_file"):
            candidates = (
                VideoFile.objects.exclude(**{field_name: ""})
                .exclude(**{f"{field_name}__isnull": True})
                .order_by("pk")
            )
            total = candidates.count()
            limit = _sample_limit(total, fast=fast, sample_size=sample_size)
            for video in candidates[:limit]:
                field_file = cast(FieldFile, getattr(video, field_name))
                self._check_encrypted_field_file(
                    issues,
                    field_file=field_file,
                    video_id=int(video.pk),
                    field_name=field_name,
                )
                checked += 1

        self.stdout.write(
            self.style.SUCCESS(f"[storage] encrypted field files checked: {checked}")
        )
        return checked

    def _check_encrypted_field_file(
        self,
        issues: list[_ReadinessIssue],
        *,
        field_file: FieldFile,
        video_id: int,
        field_name: str,
    ) -> None:
        if not _field_file_has_name(field_file):
            return

        storage = getattr(field_file, "storage", None)
        name = str(field_file.name)
        if not _has_encrypted_storage_api(storage):
            issues.append(
                _ReadinessIssue(
                    block="crypto_storage",
                    video_id=video_id,
                    message=f"{field_name} is not backed by EncryptedStorage.",
                    detail=name,
                )
            )
            return

        try:
            if not storage.is_encrypted(name):
                issues.append(
                    _ReadinessIssue(
                        block="crypto_storage",
                        video_id=video_id,
                        message=f"{field_name} is not LXENC encrypted at rest.",
                        detail=name,
                    )
                )
                return
            with storage.open(name, "rb") as handle:
                handle.read(1)
        except Exception as exc:
            issues.append(
                _ReadinessIssue(
                    block="crypto_storage",
                    video_id=video_id,
                    message=f"{field_name} could not be decrypted from storage.",
                    detail=f"{name}: {exc}",
                )
            )

    def _check_nginx_offload(self, issues: list[_ReadinessIssue]) -> None:
        protected_url = get_protected_media_url().strip()
        protected_root = get_protected_media_root().resolve()

        if not nginx_offload_enabled():
            issues.append(
                _ReadinessIssue(
                    block="nginx",
                    message="SERVE_WITH_NGINX is not enabled; HLS segments fail closed without Nginx offload.",
                )
            )
        if not protected_url.startswith("/") or "://" in protected_url:
            issues.append(
                _ReadinessIssue(
                    block="nginx",
                    message="NGINX_PROTECTED_MEDIA_URL must be an internal absolute path.",
                    detail=protected_url,
                )
            )
        if not protected_url.endswith("/"):
            issues.append(
                _ReadinessIssue(
                    block="nginx",
                    message="NGINX_PROTECTED_MEDIA_URL must end with '/'.",
                    detail=protected_url,
                )
            )
        if not protected_root.is_dir():
            issues.append(
                _ReadinessIssue(
                    block="nginx",
                    message="PROTECTED_MEDIA_ROOT does not exist or is not a directory.",
                    detail=str(protected_root),
                )
            )

        try:
            probe_response = build_nginx_accel_response_for_path(
                path=protected_root / "hls_readiness_probe.m3u8",
                content_type=_HLS_PLAYLIST_CONTENT_TYPE,
                filename="hls_readiness_probe.m3u8",
                disposition="inline",
                accept_ranges=False,
            )
            redirect_header = str(probe_response["X-Accel-Redirect"])
            if not redirect_header.startswith(protected_url.rstrip("/") + "/"):
                issues.append(
                    _ReadinessIssue(
                        block="nginx",
                        message="X-Accel-Redirect does not target the protected media location.",
                        detail=redirect_header,
                    )
                )
        except Exception as exc:
            issues.append(
                _ReadinessIssue(
                    block="nginx",
                    message="Could not build a valid X-Accel-Redirect response.",
                    detail=str(exc),
                )
            )

        if not any(issue.block == "nginx" for issue in issues):
            self.stdout.write(
                self.style.SUCCESS(
                    f"[nginx] offload active, protected url={protected_url}"
                )
            )
        else:
            self.stdout.write(self.style.ERROR("[nginx] offload/config invalid"))

    def _check_hls_artifact_completeness(
        self,
        issues: list[_ReadinessIssue],
        *,
        eligible_video_ids: list[int],
        failed_video_ids: set[int],
    ) -> None:
        if not eligible_video_ids:
            self.stdout.write(self.style.WARNING("[hls-db] no finalized videos found"))
            return

        ready_pairs = set(
            VideoHlsArtifact.objects.filter(
                video_id__in=eligible_video_ids,
                artifact_kind__in=(
                    VideoHlsArtifact.ArtifactKind.RAW.value,
                    VideoHlsArtifact.ArtifactKind.PROCESSED.value,
                ),
                status=VideoHlsArtifact.Status.READY.value,
            ).values_list("video_id", "artifact_kind")
        )
        required_pairs = [
            (video_id, artifact_kind)
            for video_id in eligible_video_ids
            for artifact_kind in (
                VideoHlsArtifact.ArtifactKind.RAW.value,
                VideoHlsArtifact.ArtifactKind.PROCESSED.value,
            )
        ]
        missing_ready = [pair for pair in required_pairs if pair not in ready_pairs]
        missing_video_ids = sorted({video_id for video_id, _ in missing_ready})
        if missing_ready:
            failed_video_ids.update(missing_video_ids)
            missing_detail = ", ".join(
                f"{video_id}:{artifact_kind}"
                for video_id, artifact_kind in missing_ready[:_MAX_REPORTED_IDS]
            )
            issues.append(
                _ReadinessIssue(
                    block="hls_db",
                    message="Videos without required READY raw/processed HLS artifacts found.",
                    detail=f"count={len(missing_ready)} video_kind={missing_detail}",
                )
            )
            self.stdout.write(
                self.style.ERROR(
                    f"[hls-db] missing READY artifacts: {len(missing_ready)}"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"[hls-db] READY raw/processed HLS artifacts present: {len(ready_pairs)}"
            )
        )

    def _check_hls_filesystem_state(
        self,
        issues: list[_ReadinessIssue],
        *,
        eligible_video_ids: list[int],
        failed_video_ids: set[int],
        fast: bool,
        sample_size: int | None,
    ) -> int:
        ready_artifacts = (
            VideoHlsArtifact.objects.filter(
                video_id__in=eligible_video_ids,
                artifact_kind__in=(
                    VideoHlsArtifact.ArtifactKind.RAW.value,
                    VideoHlsArtifact.ArtifactKind.PROCESSED.value,
                ),
                status=VideoHlsArtifact.Status.READY.value,
            )
            .select_related("video")
            .order_by("video_id", "artifact_kind")
        )
        total = ready_artifacts.count()
        limit = _sample_limit(total, fast=fast, sample_size=sample_size)
        checked = 0

        for artifact in ready_artifacts[:limit]:
            checked += 1
            try:
                playlist_path = hls_playlist_path(artifact)
                if playlist_path.stat().st_size <= 0:
                    raise RuntimeError(f"playlist is empty: {playlist_path}")
                _readable_file(playlist_path)

                content_key = unwrap_hls_content_key(artifact)
                if len(content_key) != HLS_CONTENT_KEY_BYTES:
                    raise RuntimeError("unwrapped HLS content key has invalid length")

                if int(artifact.segment_count) <= 0:
                    raise RuntimeError("artifact has no segment count")
                segment_path = hls_segment_path(artifact, "seg_000.ts")
                if segment_path.stat().st_size <= 0:
                    raise RuntimeError(f"first HLS segment is empty: {segment_path}")
                _readable_file(segment_path)
            except Exception as exc:
                video_id = int(artifact.video_id)
                failed_video_ids.add(video_id)
                issues.append(
                    _ReadinessIssue(
                        block="hls_files",
                        video_id=video_id,
                        message="READY HLS artifact failed filesystem/key sanity check.",
                        detail=str(exc),
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"[hls-files] deep-checked artifacts: {checked}")
        )
        return checked

    def _check_legacy_streamable_paths(
        self,
        issues: list[_ReadinessIssue],
        *,
        failed_video_ids: set[int],
    ) -> None:
        legacy_qs = VideoFile.objects.filter(
            Q(raw_streamable_relative_path__gt="")
            | Q(processed_streamable_relative_path__gt="")
        ).order_by("pk")
        legacy_count = legacy_qs.count()
        if not legacy_count:
            self.stdout.write(self.style.SUCCESS("[legacy] no streamable MP4 paths"))
            return

        legacy_ids = list(legacy_qs.values_list("pk", flat=True)[:_MAX_REPORTED_IDS])
        all_legacy_ids = list(legacy_qs.values_list("pk", flat=True))
        failed_video_ids.update(int(video_id) for video_id in all_legacy_ids)
        issues.append(
            _ReadinessIssue(
                block="legacy_paths",
                message=(
                    "Legacy raw_streamable_relative_path or "
                    "processed_streamable_relative_path values remain. Run "
                    "migrate_video_streamable_storage or the media storage "
                    "migration before HLS-only launch."
                ),
                detail=f"count={legacy_count} ids={_format_ids([int(value) for value in legacy_ids])}",
            )
        )
        self.stdout.write(
            self.style.ERROR(f"[legacy] streamable MP4 path residues: {legacy_count}")
        )

    def _check_api_payload_contract(
        self,
        issues: list[_ReadinessIssue],
        *,
        eligible_video_ids: list[int],
        failed_video_ids: set[int],
        fast: bool,
        sample_size: int | None,
    ) -> int:
        total = len(eligible_video_ids)
        limit = _sample_limit(total, fast=fast, sample_size=sample_size)
        sample_ids = eligible_video_ids[:limit]
        if not sample_ids:
            self.stdout.write(self.style.WARNING("[api] no video URLs to sample"))
            return 0

        videos = VideoFile.objects.filter(pk__in=sample_ids).order_by("pk")
        serializer = VideoFileSerializer(context={"request": _ReadinessRequest()})
        checked = 0
        for video in videos:
            checked += 1
            url_value = serializer.get_video_url(cast(Any, video))
            if not isinstance(url_value, str):
                failed_video_ids.add(int(video.pk))
                issues.append(
                    _ReadinessIssue(
                        block="api_contract",
                        video_id=int(video.pk),
                        message="VideoFileSerializer did not return a string playback URL.",
                        detail=str(url_value),
                    )
                )
                continue

            split_url = urlsplit(url_value)
            if "/stream/" in split_url.path or not split_url.path.endswith(
                "/hls/playlist.m3u8"
            ):
                failed_video_ids.add(int(video.pk))
                issues.append(
                    _ReadinessIssue(
                        block="api_contract",
                        video_id=int(video.pk),
                        message=(
                            "VideoFileSerializer playback URL is not an HLS playlist URL."
                        ),
                        detail=url_value,
                    )
                )

        self.stdout.write(self.style.SUCCESS(f"[api] sampled video URLs: {checked}"))
        return checked

    def _write_human_summary(self, summary: _CheckSummary) -> None:
        for issue in summary.issues:
            video = f" video={issue.video_id}" if issue.video_id is not None else ""
            detail = f" ({issue.detail})" if issue.detail else ""
            self.stdout.write(
                self.style.ERROR(f"[{issue.block}]{video} {issue.message}{detail}")
            )

        message = (
            "Pruefung abgeschlossen: "
            f"{summary.valid_videos} Videos valide, "
            f"{summary.failed_videos} fehlerhaft, "
            f"{len(summary.issues)} Blocker."
        )
        if summary.issues:
            self.stdout.write(self.style.ERROR(message))
        else:
            self.stdout.write(self.style.SUCCESS(message))
