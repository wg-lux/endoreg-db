"""Restore Full Export records onto verified, encrypted processed media."""

import logging
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from django.db import transaction
from django.db.models import Q
from lx_dtypes.models.contracts.video_provenance import VideoProvenanceRecovery

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.information_source import (
    InformationSource,
    InformationSourceManager,
)
from endoreg_db.schemas.persisted_json import (
    AnnotationExportImportReceipt,
    VideoFileMetaPayload,
)
from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    FramePresentationTimestamp,
    VideoArtifactProbe,
)
from endoreg_db.services.datasets.annotation_archive import (
    ExportVideo,
    open_export,
    read_export,
)
from endoreg_db.services.hub.deployment import get_deployment_role
from endoreg_db.services.hub.video_provenance import accept_video_provenance_recovery
from endoreg_db.services.media.state import update_media_state
from endoreg_db.services.streaming.hls_media import (
    hls_result_is_ready,
    materialize_video_hls,
)
from endoreg_db.services.video_files.frame_coordinates import (
    validate_frame_timeline_coordinate,
)
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.services.video_storage.contracts import configured_video_storage_profile
from endoreg_db.services.video_storage.probes import (
    probe_local_mp4,
    probe_video_frame_pts,
)
from endoreg_db.services.video_storage.timelines import persist_video_source_timeline
from endoreg_db.services.video_storage.validation import (
    assert_normalization_source_supported,
    assert_storage_compliance,
)
from endoreg_db.utils.file_operations import (
    advisory_file_lock,
    protected_temporary_directory,
)
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import (
    get_runtime_paths,
    resolve_protected_media_path,
    resolve_protected_runtime_path,
    to_storage_relative,
)
from endoreg_db.utils.rust_backend import is_lx_encrypted_file
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.storage.files import canonical_media_name
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnnotationImportResult:
    videos: int
    annotations: int
    segments: int
    reused_videos: int


def _verify_video(
    source: ExportVideo, path: Path
) -> tuple[
    VideoArtifactProbe, list[FramePresentationTimestamp], CanonicalFrameTimeline
]:
    digest = get_file_hash(path)
    if digest != source.manifest.artifact_sha256:
        raise ValueError(
            f"Processed video hash differs from manifest for video {source.manifest.video_id}"
        )
    profile = configured_video_storage_profile()
    time_base = (
        source.manifest.stream_time_base_num,
        source.manifest.stream_time_base_den,
    )

    def check_artifact(probe: VideoArtifactProbe) -> None:
        assert_normalization_source_supported(source=probe, profile=profile)
        assert_storage_compliance(probe, profile=profile)
        if (probe.timeline.time_base_num, probe.timeline.time_base_den) != time_base:
            raise ValueError("Decoded time base differs from the export manifest")

    probe, timestamps = probe_local_mp4(path, check_artifact=check_artifact)
    timeline = CanonicalFrameTimeline(
        content_hash=digest,
        time_base_num=time_base[0],
        time_base_den=time_base[1],
        presentation_timestamps=[item.presentation_timestamp for item in timestamps],
    )
    for row in source.annotations:
        validate_frame_timeline_coordinate(
            timeline,
            row.frame_number,
            row.presentation_timestamp,
            row.presentation_time_seconds,
        )
    for segment in source.segments:
        validate_frame_timeline_coordinate(
            timeline,
            segment.start_frame_number,
            segment.start_presentation_timestamp,
            segment.start_presentation_time_seconds,
        )
        validate_frame_timeline_coordinate(
            timeline,
            segment.end_frame_number,
            segment.end_presentation_timestamp,
            segment.end_presentation_time_seconds,
        )
    return probe, timestamps, timeline


def _storage_name(source: ExportVideo) -> str:
    return to_storage_relative(
        get_runtime_paths().anonym_video
        / "offline_exports"
        / canonical_media_name(
            source.raw_hash, ".mp4", generation=source.manifest.artifact_sha256
        )
    )


def _check_stored_video(video: VideoFile, name: str, digest: str) -> None:
    if (
        video.processed_file.name != name
        or not is_lx_encrypted_file(resolve_protected_media_path(name))
        or get_file_hash(video.processed_file) != digest
    ):
        raise ValueError(
            "Stored processed media is missing or differs from the verified export"
        )


def _existing_video(
    source: ExportVideo,
    center: Center,
    receipt: AnnotationExportImportReceipt,
    *,
    lock: bool,
) -> VideoFile | None:
    candidates = VideoFile.objects.filter(
        Q(raw_video_hash=source.raw_hash)
        | Q(processed_video_hash=source.manifest.artifact_sha256)
    )
    if lock:
        candidates = candidates.select_for_update()
    existing = list(candidates.order_by("pk"))
    if not existing:
        return None
    video = existing[0]
    if (
        len(existing) != 1
        or video.center_id != center.pk
        or video.raw_video_hash != source.raw_hash
        or video.processed_video_hash != source.manifest.artifact_sha256
        or VideoFileMetaPayload.model_validate(
            video.meta or {}
        ).annotation_export_import
        != receipt
    ):
        raise ValueError(
            "An existing video conflicts with this export or belongs to another center"
        )
    _check_stored_video(video, _storage_name(source), source.manifest.artifact_sha256)
    return video


def _restore_annotations(
    video: VideoFile,
    source: ExportVideo,
    source_names: dict[int, str],
    reviewer_tracks: dict[str, str],
) -> None:
    labels = {
        name: Label.get_or_create_from_name(name)[0]
        for name in sorted(
            {row.label_name for row in source.annotations}
            | {segment.label_name for segment in source.segments}
        )
    }
    information_sources = {
        key: cast(
            InformationSourceManager, InformationSource.objects
        ).get_or_create_by_name(source_names[key])[0]
        for key in sorted(
            {row.information_source_id for row in source.annotations}
            | {
                segment.information_source_id
                for segment in source.segments
                if segment.information_source_id is not None
                and segment.information_source_id in source_names
            }
        )
    }
    frames = dict(Frame.objects.filter(video=video).values_list("frame_number", "pk"))
    namespace = f"offline_export:{source.raw_hash}"
    ImageClassificationAnnotation.objects.bulk_create(
        [
            ImageClassificationAnnotation(
                frame_id=frames[row.frame_number],
                label=labels[row.label_name],
                information_source=information_sources[row.information_source_id],
                annotator=reviewer_tracks.get(row.annotator),
                value=row.value,
                float_value=row.float_value,
                external_annotation_id=f"{namespace}:{row.annotation_id}",
            )
            for row in source.annotations
        ],
        batch_size=2000,
    )
    for segment in source.segments:
        LabelVideoSegment.objects.create(
            video_file=video,
            label=labels[segment.label_name],
            source=(
                information_sources.get(segment.information_source_id)
                if segment.information_source_id is not None
                else None
            ),
            start_frame_number=segment.start_frame_number,
            end_frame_number=segment.end_frame_number + 1,
            source_node_key=namespace,
            source_segment_id=str(segment.segment_id),
        )


@transaction.atomic(durable=True)
def _publish_video(
    source: ExportVideo,
    path: Path,
    center: Center,
    receipt: AnnotationExportImportReceipt,
    source_names: dict[int, str],
    reviewer_tracks: dict[str, str],
    probe: VideoArtifactProbe,
    timestamps: list[FramePresentationTimestamp],
    timeline: CanonicalFrameTimeline,
) -> tuple[VideoFile, bool]:
    existing = _existing_video(source, center, receipt, lock=True)
    if existing is not None:
        return existing, True
    video = VideoFile(
        center=center,
        raw_video_hash=source.raw_hash,
        processed_video_hash=timeline.content_hash,
        suffix=".mp4",
        original_file_name=source.manifest.video_file_name,
        width=probe.width,
        height=probe.height,
        fps=probe.timeline.fps,
        duration=probe.timeline.duration_seconds,
        storage_mode=VideoFile.StorageMode.ENCRYPTED,
        meta={"annotation_export_import": receipt.model_dump(mode="json")},
    )
    name = _storage_name(source)
    video.processed_file.name = name
    # A crash may leave this deterministic encrypted file without a database
    # row. Only reuse the exact authenticated bytes at the converter's path.
    if not video.processed_file.storage.exists(name):
        save_local_file(video.processed_file, path, name=name)
    _check_stored_video(video, name, timeline.content_hash)
    accept_video_provenance_recovery(
        video,
        VideoProvenanceRecovery(
            method="decoded_processed_timeline_v1",
            recording_hash=source.raw_hash,
            processed_timeline=timeline,
        ),
    )
    video.save()
    persist_video_source_timeline(
        video,
        path,
        probe_artifact=lambda _path: probe,
        probe_frame_timestamps=lambda _path: timestamps,
        probe_frame_pts=probe_video_frame_pts,
        artifact_kind=VideoArtifactKind.PROCESSED,
    )
    _restore_annotations(video, source, source_names, reviewer_tracks)
    state = video.get_or_create_state()
    update_media_state(
        state,
        changes={
            "processing_started": True,
            "anonymized": True,
            "sensitive_meta_processed": True,
            "anonymization_validated": True,
            "processed_file_sha256": timeline.content_hash,
            "frame_annotations_generated": True,
        },
    )
    return video, False


def import_annotation_export(
    path: str | Path, *, center: Center, apply: bool = False
) -> AnnotationImportResult:
    """Verify an approved local export and commit each video as one unit."""
    if get_deployment_role() not in {"standalone", "local_study_server"}:
        raise ValueError(
            "Offline export import requires a standalone or local study server"
        )
    paths = get_runtime_paths()
    selected = resolve_protected_runtime_path(path, fallback=paths.migration_staging)
    lock = (
        advisory_file_lock(lock_path=paths.locks / "offline_annotation_import.lock")
        if apply
        else nullcontext()
    )
    with (
        lock,
        open_export(selected) as files,
        protected_temporary_directory(prefix="annotation-import-") as scratch,
    ):
        videos, source_names, manifest_hash, table_hash = read_export(files, scratch)
        reviewer_tracks = {
            name: f"offline_export:{table_hash}:{index}"
            for index, name in enumerate(
                sorted(
                    {
                        row.annotator
                        for video in videos
                        for row in video.annotations
                        if row.annotator
                    }
                ),
                start=1,
            )
        }
        if any(
            len({key for key in keys}) != len(videos)
            for keys in (
                (item.raw_hash for item in videos),
                (item.manifest.artifact_sha256 for item in videos),
            )
        ):
            raise ValueError("The export repeats a raw or processed video hash")
        reused = 0
        for source in videos:
            receipt = AnnotationExportImportReceipt(
                manifest_sha256=manifest_hash,
                table_sha256=table_hash,
                source_video_id=source.manifest.video_id,
            )
            with protected_temporary_directory(
                prefix="annotation-video-"
            ) as video_scratch:
                media = video_scratch / "video.mp4"
                files.stage(f"videos/{source.manifest.video_file_name}", media)
                probe, timestamps, timeline = _verify_video(source, media)
                if not apply:
                    reused += (
                        _existing_video(source, center, receipt, lock=False) is not None
                    )
                    continue
                video, already_imported = _publish_video(
                    source,
                    media,
                    center,
                    receipt,
                    source_names,
                    reviewer_tracks,
                    probe,
                    timestamps,
                    timeline,
                )
            # Streaming failure leaves this exact database import replayable.
            streaming = materialize_video_hls(video.pk)
            if not hls_result_is_ready(streaming.status):
                raise ValueError(
                    f"Video {video.pk} was restored but streaming is {streaming.status}; retry the import"
                )
            reused += already_imported
            emit_structured_event(
                logger,
                "offline_annotation_export.imported",
                video_id=video.pk,
                annotation_count=len(source.annotations),
                segment_count=len(source.segments),
                reused=already_imported,
            )
    return AnnotationImportResult(
        videos=len(videos),
        annotations=sum(len(item.annotations) for item in videos),
        segments=sum(len(item.segments) for item in videos),
        reused_videos=reused,
    )
