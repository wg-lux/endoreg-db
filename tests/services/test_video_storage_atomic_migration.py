from __future__ import annotations

import errno
import logging
from pathlib import Path

import pytest

from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_storage import normalization
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    VideoStorageProfile,
)
from endoreg_db.services.video_storage.validation import validate_normalized_output
from endoreg_db.utils import file_operations as legacy_file_operations
from endoreg_db.utils.filesystem import file_operations as canonical_file_operations

pytestmark = pytest.mark.unit


def test_canonical_filesystem_facade_reuses_audited_implementation() -> None:
    assert (
        canonical_file_operations.atomic_move_file
        is legacy_file_operations.atomic_move_file
    )
    assert (
        canonical_file_operations.safe_unlink_file
        is legacy_file_operations.safe_unlink_file
    )


def _profile() -> VideoStorageProfile:
    return VideoStorageProfile(
        name="atomic-migration-test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )


def _probe(*, compliant: bool) -> VideoArtifactProbe:
    return VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1280,
        height=720,
        bit_rate_bps=800_000 if compliant else 20_000_000,
        size_bytes=1_000_000 if compliant else 20_000_000,
        timeline=VideoTimelineContract(
            fps_num=25,
            fps_den=1,
            duration_seconds=10.0,
            frame_count=250,
            variable_frame_rate=False,
        ),
    )


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    reference_path = tmp_path / "raw.mp4"
    input_path = tmp_path / "anonymized.mp4"
    reference_path.write_bytes(b"raw-source")
    input_path.write_bytes(b"existing-valid-master")
    return reference_path, input_path


def _staging_files(tmp_path: Path) -> list[Path]:
    return list(tmp_path.glob(".*.storage-normalization.*.part.mp4"))


def _probe_for_normalization(
    path: Path,
    *,
    reference_path: Path,
    input_path: Path,
) -> VideoArtifactProbe:
    if path == reference_path:
        return _probe(compliant=True)
    if path == input_path:
        return _probe(compliant=False)
    return _probe(compliant=True)


def _normalize(
    *,
    reference_path: Path,
    input_path: Path,
) -> VideoStorageNormalizationEvidence:
    def probe(path: Path) -> VideoArtifactProbe:
        return _probe_for_normalization(
            path,
            reference_path=reference_path,
            input_path=input_path,
        )

    return normalization.normalize_video_file(
        input_path=input_path,
        reference_path=reference_path,
        quality_mode="quality",
        profile=_profile(),
        segments=None,
        force_cpu=True,
        probe_artifact=probe,
        validate_output=validate_normalized_output,
    )


def test_disk_full_during_transcode_preserves_master_and_removes_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference_path, input_path = _paths(tmp_path)

    def fail_with_disk_full(
        *,
        input_path: Path,
        output_path: Path,
        **_kwargs: object,
    ) -> Path:
        assert output_path.parent == input_path.parent
        assert output_path.name.startswith(".anonymized.storage-normalization.")
        output_path.write_bytes(b"partial-output")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "transcode_video",
        fail_with_disk_full,
    )
    caplog.set_level(logging.INFO)

    with pytest.raises(OSError) as exc_info:
        _normalize(reference_path=reference_path, input_path=input_path)

    assert exc_info.value.errno == errno.ENOSPC
    assert input_path.read_bytes() == b"existing-valid-master"
    assert _staging_files(tmp_path) == []
    assert "video_storage.normalization_failed" in caplog.text
    assert '"error_type": "OSError"' in caplog.text
    assert '"operation": "unlink"' in caplog.text


def test_interruption_before_validation_preserves_master_and_cleans_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference_path, input_path = _paths(tmp_path)

    def interrupt_transcode(
        *,
        output_path: Path,
        **_kwargs: object,
    ) -> Path:
        output_path.write_bytes(b"partial-output")
        raise KeyboardInterrupt("controlled interruption")

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "transcode_video",
        interrupt_transcode,
    )
    caplog.set_level(logging.INFO)

    with pytest.raises(KeyboardInterrupt, match="controlled interruption"):
        _normalize(reference_path=reference_path, input_path=input_path)

    assert input_path.read_bytes() == b"existing-valid-master"
    assert _staging_files(tmp_path) == []
    assert "video_storage.normalization_failed" in caplog.text
    assert '"error_type": "KeyboardInterrupt"' in caplog.text


def test_integrity_gate_failure_never_calls_atomic_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reference_path, input_path = _paths(tmp_path)
    publication_called = False

    def transcode_candidate(*, output_path: Path, **_kwargs: object) -> Path:
        output_path.write_bytes(b"complete-but-invalid-output")
        return output_path

    def reject_candidate(
        *,
        source: VideoArtifactProbe,
        output: VideoArtifactProbe,
        profile: VideoStorageProfile,
        segments: object,
    ) -> VideoStorageNormalizationEvidence:
        del source, output, profile, segments
        raise VideoStorageNormalizationError(
            "controlled timeline and integrity mismatch"
        )

    def fail_if_published(*, source: Path, destination: Path) -> Path:
        del source, destination
        nonlocal publication_called
        publication_called = True
        raise AssertionError("invalid candidate must never be published")

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "transcode_video",
        transcode_candidate,
    )
    monkeypatch.setattr(normalization, "atomic_move_file", fail_if_published)

    def probe(path: Path) -> VideoArtifactProbe:
        return _probe_for_normalization(
            path,
            reference_path=reference_path,
            input_path=input_path,
        )

    with pytest.raises(
        VideoStorageNormalizationError,
        match="controlled timeline and integrity mismatch",
    ):
        normalization.normalize_video_file(
            input_path=input_path,
            reference_path=reference_path,
            quality_mode="quality",
            profile=_profile(),
            segments=None,
            force_cpu=True,
            probe_artifact=probe,
            validate_output=reject_candidate,
        )

    assert publication_called is False
    assert input_path.read_bytes() == b"existing-valid-master"
    assert _staging_files(tmp_path) == []


def test_atomic_publication_failure_preserves_existing_master(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference_path, input_path = _paths(tmp_path)

    def transcode_candidate(*, output_path: Path, **_kwargs: object) -> Path:
        output_path.write_bytes(b"validated-candidate")
        return output_path

    def fail_atomic_publication(*, source: Path, destination: Path) -> Path:
        assert source.parent == destination.parent
        assert destination == input_path
        raise OSError(errno.EIO, "controlled atomic publication failure")

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "transcode_video",
        transcode_candidate,
    )
    monkeypatch.setattr(
        normalization,
        "atomic_move_file",
        fail_atomic_publication,
    )
    caplog.set_level(logging.INFO)

    with pytest.raises(OSError) as exc_info:
        _normalize(reference_path=reference_path, input_path=input_path)

    assert exc_info.value.errno == errno.EIO
    assert input_path.read_bytes() == b"existing-valid-master"
    assert _staging_files(tmp_path) == []
    assert "video_storage.normalization_candidate_validated" in caplog.text
    assert "video_storage.normalization_published" not in caplog.text
    assert "video_storage.normalization_failed" in caplog.text
