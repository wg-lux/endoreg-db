"""Configure lx-anonymizer with host-owned execution and provenance persistence."""

# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
from __future__ import annotations

import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from lx_anonymizer.frame_cleaner import FrameCleaner, FrameCleanerQualityProfile
from lx_anonymizer.runtime_types import VideoExecutionHooks
from lx_dtypes.models.contracts.video_provenance import VideoFrameProvenancePayload

from endoreg_db.services.video_storage.canonical_timelines import (
    capture_canonical_timeline,
)
from endoreg_db.services.video_storage.frame_provenance import persist_frame_provenance
from endoreg_db.services.video_storage.probes import probe_video_artifact
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.transcode_execution import _run_ffmpeg_command


@contextmanager
def _observe_decoded_frames(
    input_video: Path, output_video: Path
) -> Generator[Callable[[int], None], None, None]:
    source = capture_canonical_timeline(
        input_video, probe_video_artifact(input_video), require_original_pts=True
    )
    indices: list[int] = []
    yield indices.append
    if len(indices) != len(source.presentation_timestamps) or any(
        index != position for position, index in enumerate(indices)
    ):
        raise ValueError("Detector did not process the complete source frame sequence")
    if get_file_hash(input_video) != source.content_hash:
        raise ValueError("Detector source changed during masking")
    output = capture_canonical_timeline(
        output_video, probe_video_artifact(output_video), require_original_pts=True
    )
    persist_frame_provenance(
        VideoFrameProvenancePayload(
            method="opencv_decoder_input_v1",
            source=source,
            output=output,
            source_frame_indices=indices,
        )
    )


def _run_encoder(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # Host storage policy owns timestamp fidelity and the bounded process timeout.
    # Newer lx-anonymizer commands already carry these options; add only missing ones.
    timeline_options: list[str] = []
    if "-enc_time_base" not in cmd:
        timeline_options.extend(["-enc_time_base", "demux"])
    if "-fps_mode" not in cmd:
        timeline_options.extend(["-fps_mode", "passthrough"])
    input_options = [] if "-copyts" in cmd else ["-copyts"]
    command = [
        cmd[0],
        *input_options,
        *cmd[1:-1],
        *timeline_options,
        cmd[-1],
    ]
    returncode, stderr = _run_ffmpeg_command(command)
    if returncode:
        raise subprocess.CalledProcessError(returncode, command, stderr=stderr)
    return subprocess.CompletedProcess(command, returncode, stdout="", stderr=stderr)


def create_frame_cleaner(
    *, quality_profile: FrameCleanerQualityProfile | str | None = None
) -> FrameCleaner:
    """Return the library cleaner; one hook configuration belongs to one attempt."""
    return FrameCleaner(
        quality_profile=quality_profile,
        execution_hooks=VideoExecutionHooks(
            run_encoder=_run_encoder, observe_decoded_frames=_observe_decoded_frames
        ),
    )
