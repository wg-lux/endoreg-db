"""Capture actual decoder ancestry at the encoder boundary and retain it in SQL."""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from lx_dtypes.models.contracts.video_provenance import (
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
    VideoProvenanceBundle,
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
