"""Capture actual decoder ancestry at the encoder boundary and retain it in SQL."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lx_dtypes.models.contracts.video_provenance import (
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)

from endoreg_db.services.video_storage.canonical_timelines import (
    capture_canonical_timeline,
)
from endoreg_db.services.video_storage.probes import probe_video_artifact
from endoreg_db.utils.file_operations import (
    atomic_create_file,
    protected_temporary_directory,
)
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


logger = logging.getLogger(__name__)

# Canonical masters are MP4; their muxer otherwise rescales coarse time bases.
_TRACK_TIMESCALE_OUTPUT_SUFFIXES = frozenset({".mp4", ".m4v", ".mov"})

type PresentationTimestampIdentity = Literal[
    "identical_ticks", "identical_instants", "changed"
]


class PresentationTimestampChangedError(ValueError):
    """A timestamp-passthrough transcode altered recorded presentation timestamps."""


def presentation_timestamp_identity(
    source: VideoFrameTimeline,
    output: VideoFrameTimeline,
    source_frame_indices: Sequence[int],
) -> PresentationTimestampIdentity:
    """Compare every output frame with the source frame it was decoded from.

    ``identical_ticks`` means the same time base and integer timestamps, so
    recorded values can be used unchanged; ``identical_instants`` means equal
    rational times in a different time base.
    """
    if len(source_frame_indices) != len(output.presentation_timestamps):
        raise ValueError("Frame ancestry does not cover every output frame")
    pairs = [
        (output.presentation_timestamps[index], source.presentation_timestamps[origin])
        for index, origin in enumerate(source_frame_indices)
    ]
    if (output.time_base_num, output.time_base_den) == (
        source.time_base_num,
        source.time_base_den,
    ) and all(output_tick == source_tick for output_tick, source_tick in pairs):
        return "identical_ticks"
    output_base = Fraction(output.time_base_num, output.time_base_den)
    source_base = Fraction(source.time_base_num, source.time_base_den)
    if all(
        output_tick * output_base == source_tick * source_base
        for output_tick, source_tick in pairs
    ):
        return "identical_instants"
    return "changed"


def _is_timestamp_passthrough(command: Sequence[str]) -> bool:
    if "-copyts" not in command or "-fpsmax" in command:
        return False
    return any(
        flag == "-fps_mode" and value == "passthrough"
        for flag, value in zip(command, command[1:])
    )


def _mp4_track_timescale(timeline: VideoFrameTimeline) -> int:
    """Timescale that stores the source ticks unchanged in an MP4 track."""
    numerator, denominator = timeline.time_base_num, timeline.time_base_den
    if denominator % numerator:
        raise PresentationTimestampChangedError(
            f"Time base {numerator}/{denominator} cannot be stored as an MP4 "
            "track timescale without changing presentation timestamps"
        )
    return denominator // numerator


def _with_source_track_timescale(
    command: list[str], *, source: VideoFrameTimeline
) -> list[str]:
    if "-video_track_timescale" in command:
        return command
    timescale = _mp4_track_timescale(source)
    return [*command[:-1], "-video_track_timescale", str(timescale), command[-1]]


def presentation_timestamps_are_stored(source: VideoFrameTimeline) -> bool:
    """Whether the source is a published processed generation whose timestamps
    are the video's persisted frame coordinates.

    Import and anonymization transcode media whose processed timestamps are not
    stored yet; their frame rows are remapped to the new generation afterwards.
    """
    from endoreg_db.models.media.video.video_file import VideoFile

    return VideoFile.objects.filter(
        processed_video_hash=source.content_hash,
        meta__source_timeline__content_hash=source.content_hash,
    ).exists()


def encoder_provenance_args(path: Path) -> list[str]:
    return [
        "-stats_enc_pre:v:0",
        str(path),
        "-stats_enc_pre_fmt:v:0",
        "{n} {ni} {pts} {tb} {ptsi} {tbi}",
    ]


def read_encoder_provenance(
    path: Path,
    *,
    source: VideoFrameTimeline,
    output: VideoFrameTimeline,
    allow_container_offset: bool = False,
    allow_decoder_offset: bool = False,
) -> VideoFrameProvenancePayload:
    """Validate every encoder-input row against independently decoded media.

    ``ni`` is FFmpeg's input decoder frame index carried through frame filters.
    Timestamp proximity never establishes source identity. B-frame packet order
    is deliberately not used.
    """
    indices: list[int] = []
    source_base = Fraction(source.time_base_num, source.time_base_den)
    output_base = Fraction(output.time_base_num, output.time_base_den)
    offset = Fraction(0)
    decoder_offset = Fraction(0)
    with path.open(encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            fields = line.split()
            if len(fields) != 6 or index >= len(output.presentation_timestamps):
                raise ValueError("Invalid encoder frame provenance length")
            number, source_index, encoded_pts = (int(value) for value in fields[:3])
            encoded_base = Fraction(fields[3])
            input_pts, input_base = int(fields[4]), Fraction(fields[5])
            if number != index or not 0 <= source_index < len(
                source.presentation_timestamps
            ):
                raise ValueError("Missing or invalid encoder source frame identity")
            if encoded_base <= 0 or input_base <= 0 or input_pts == 9223372036854775807:
                raise ValueError("Missing encoder timestamp provenance")
            source_time = source.presentation_timestamps[source_index] * source_base
            if index == 0 and allow_decoder_offset:
                decoder_offset = source_time - input_pts * input_base
            if input_pts * input_base + decoder_offset != source_time:
                raise ValueError("Encoder source timestamp disagrees with ffprobe")
            output_time = output.presentation_timestamps[index] * output_base
            encoded_time = encoded_pts * encoded_base
            if index == 0 and allow_container_offset:
                offset = output_time - encoded_time
            if abs(output_time - encoded_time - offset) > output_base / 2:
                raise ValueError(
                    "Encoded frame does not match decoded output presentation timestamp"
                )
            indices.append(source_index)
    return VideoFrameProvenancePayload(
        source=source,
        output=output,
        source_frame_indices=indices,
        decoder_timestamp_offset_num=decoder_offset.numerator,
        decoder_timestamp_offset_den=decoder_offset.denominator,
        encoder_timestamp_offset_num=offset.numerator,
        encoder_timestamp_offset_den=offset.denominator,
    )


def persist_frame_provenance(proof: VideoFrameProvenancePayload) -> None:
    from endoreg_db.models.media.video.frame_provenance import VideoFrameProvenance

    row, _ = VideoFrameProvenance.objects.get_or_create(
        source_content_hash=proof.source.content_hash,
        output_content_hash=proof.output.content_hash,
        defaults={"payload": proof.model_dump(mode="json")},
    )
    if VideoFrameProvenancePayload.model_validate(row.payload) != proof:
        raise ValueError("Conflicting frame provenance for the same content identities")


def run_with_frame_provenance(
    command: list[str],
    runner: Callable[[list[str]], tuple[int, str]],
) -> tuple[int, str]:
    """One local-file transcode attempt; failed attempts never create evidence."""
    if command.count("-i") != 1:
        raise ValueError("Frame provenance requires one explicit video input")
    source_path = Path(command[command.index("-i") + 1])
    output_path = Path(command[-1])
    source = capture_canonical_timeline(
        source_path, probe_video_artifact(source_path), require_original_pts=True
    )
    # Recorded timestamps are matched against external clocks, so passthrough
    # transcodes to canonical MP4 keep the exact source ticks. A change is fatal
    # only for stored timestamps; otherwise it is logged and recorded in the
    # provenance receipt while frame rows are remapped to the new generation.
    preserve_ticks = (
        _is_timestamp_passthrough(command)
        and output_path.suffix.lower() in _TRACK_TIMESCALE_OUTPUT_SUFFIXES
    )
    # The database is consulted only when a change actually occurs.
    if preserve_ticks:
        try:
            command = _with_source_track_timescale(command, source=source)
        except PresentationTimestampChangedError:
            if presentation_timestamps_are_stored(source):
                raise
            emit_structured_event(
                logger,
                "transcode_track_timescale_unrepresentable",
                level=logging.WARNING,
                source_content_hash=source.content_hash,
                time_base_num=source.time_base_num,
                time_base_den=source.time_base_den,
            )
    with protected_temporary_directory(prefix="frame-provenance-") as directory:
        stats_path = directory / "encoder.txt"
        atomic_create_file(destination=stats_path, content=(), file_mode=0o600)
        result = runner(
            [*command[:-1], *encoder_provenance_args(stats_path), command[-1]]
        )
        if result[0] != 0:
            return result
        if get_file_hash(source_path) != source.content_hash:
            raise ValueError("Source content changed during frame provenance capture")
        output = capture_canonical_timeline(
            output_path, probe_video_artifact(output_path), require_original_pts=True
        )
        proof = read_encoder_provenance(
            stats_path,
            source=source,
            output=output,
            allow_decoder_offset="-copyts" not in command,
        )
        if preserve_ticks:
            identity = presentation_timestamp_identity(
                proof.source, proof.output, proof.source_frame_indices
            )
            if identity != "identical_ticks":
                if presentation_timestamps_are_stored(source):
                    raise PresentationTimestampChangedError(
                        "Transcode changed stored presentation timestamps "
                        f"({identity}); the output is not published"
                    )
                emit_structured_event(
                    logger,
                    "transcode_presentation_timestamps_changed",
                    level=logging.WARNING,
                    source_content_hash=source.content_hash,
                    output_content_hash=output.content_hash,
                    identity=identity,
                )
        persist_frame_provenance(proof)
        return result


def provenance_chain(*, source_hash: str, output_hash: str) -> VideoProvenanceBundle:
    """Resolve exactly one bounded ancestry chain; ambiguous ancestry fails closed."""
    from endoreg_db.models.media.video.frame_provenance import VideoFrameProvenance

    def visit(
        current: str, seen: frozenset[str]
    ) -> list[list[VideoFrameProvenancePayload]]:
        if current == source_hash:
            return [[]]
        if current in seen or len(seen) >= 128:
            raise ValueError("Cyclic or excessive frame provenance history")
        chains: list[list[VideoFrameProvenancePayload]] = []
        rows = VideoFrameProvenance.objects.filter(output_content_hash=current).exclude(
            source_content_hash=current
        )
        candidates = list(rows[:129])
        if len(candidates) > 128:
            raise ValueError("Excessive frame provenance ancestry")
        for row in candidates:
            proof = VideoFrameProvenancePayload.model_validate(row.payload)
            for parent in visit(proof.source.content_hash, seen | {current}):
                chains.append([*parent, proof])
                if len(chains) > 1:
                    raise ValueError("Ambiguous frame provenance ancestry")
        return chains

    chains = visit(output_hash, frozenset())
    if len(chains) != 1 or not chains[0]:
        raise ValueError("Actual frame provenance is unavailable for this recording")
    return VideoProvenanceBundle(transformations=chains[0])


def resolve_video_provenance(
    video: VideoFile,
) -> VideoProvenanceBundle | VideoProvenanceRecovery:
    """Ancestry of the current processed generation, or its unresolved receipt.

    Read-only; callers own media validation and leases. Missing or stale
    ancestry raises instead of degrading to timestamp matching.
    """
    from endoreg_db.schemas.persisted_json import VideoFileMetaPayload

    meta = VideoFileMetaPayload.model_validate(video.meta or {})
    recovery = meta.video_provenance_recovery
    if recovery is not None:
        if recovery.recording_hash != video.raw_video_hash or (
            recovery.processed_timeline.content_hash != video.processed_video_hash
        ):
            raise ValueError("Unresolved provenance acknowledgment is stale")
        return recovery
    history = meta.canonical_timeline_history or []
    source_hash = history[0].before.content_hash if history else video.raw_video_hash
    existing = meta.video_frame_provenance
    if (
        existing is not None
        and existing.transformations[0].source.content_hash != source_hash
    ):
        raise ValueError("Frame provenance belongs to another recording")
    if existing is not None:
        previous_hash = existing.transformations[-1].output.content_hash
        if previous_hash == video.processed_video_hash:
            return existing
        continuation = provenance_chain(
            source_hash=previous_hash, output_hash=video.processed_video_hash
        )
        return VideoProvenanceBundle(
            transformations=[*existing.transformations, *continuation.transformations],
            synchronization=existing.synchronization,
        )
    bundle = provenance_chain(
        source_hash=source_hash, output_hash=video.processed_video_hash
    )
    return VideoProvenanceBundle(
        transformations=bundle.transformations,
        synchronization=meta.recording_synchronization,
    )
