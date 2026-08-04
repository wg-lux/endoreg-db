from __future__ import annotations

# pyright: reportPrivateUsage=false
from types import SimpleNamespace
from unittest.mock import patch
from typing import Any, cast

import pytest

import endoreg_db.services.hub.media_integrity as media_integrity
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityExpectation,
    MediaIntegrityResult,
    MediaIntegrityStatus,
    check_upload_job_media_integrity,
    check_report_media_integrity,
    check_video_media_integrity,
)


def _always_true(_file: object) -> bool:
    return True


def _always_exists(_path: object) -> bool:
    return True


def _exists_always(_name: str) -> bool:
    return True


class _FakeFieldFile:
    def __init__(self, name: str | None) -> None:
        self.name = name
        self.storage = SimpleNamespace(exists=_exists_always)


def _fake_video(
    *,
    pk: int,
    video_hash: str,
    raw_file_name: str | None,
    processed_file_name: str | None,
    state_validated: bool,
) -> VideoFile:
    return cast(
        VideoFile,
        SimpleNamespace(
            pk=pk,
            video_hash=video_hash,
            raw_file=_FakeFieldFile(raw_file_name),
            processed_file=_FakeFieldFile(processed_file_name),
            state=SimpleNamespace(anonymization_validated=state_validated),
        ),
    )


class _FakeQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    def select_related(self, *_args: object, **_kwargs: object) -> "_FakeQuery":
        return self

    def filter(self, **_kwargs: object) -> "_FakeQuery":
        return self

    def first(self) -> object:
        return self._value


class _FakeUploadJob:
    def __init__(
        self,
        *,
        content_hash: str,
        content_type: str,
        source_system: str,
        storage_tier: str,
        processing_provenance: dict[str, Any] | None = None,
    ) -> None:
        self.content_hash = content_hash
        self.content_type = content_type
        self.source_system = source_system
        self.storage_tier = storage_tier
        self.processing_provenance = processing_provenance or {}


def test_required_artifacts_are_readable_reports_missing_and_unreadable() -> None:
    file_ok = _FakeFieldFile("ok.mp4")
    file_missing = _FakeFieldFile("")
    with patch.object(media_integrity, "field_file_is_readable", _always_true):
        status, artifacts = media_integrity._required_artifacts_are_readable(
            (
                ("processed_file", file_ok),
                ("raw_file", file_missing),
            )
        )

    assert status == MediaIntegrityStatus.ARTIFACT_MISSING
    assert artifacts == ("raw_file",)


def test_video_integrity_failure_allows_existing_reprocessing_for_recoverable_status() -> (
    None
):
    result = MediaIntegrityResult(
        ok=False,
        status=MediaIntegrityStatus.STATE_NOT_VALIDATED,
        reason="state missing",
        content_hash="abc",
        missing_artifacts=("foo",),
    )
    assert (
        media_integrity.video_integrity_failure_allows_existing_video_reprocessing(
            result
        )
        is True
    )


def test_video_integrity_failure_disallows_reprocessing_when_not_recoverable() -> None:
    result = MediaIntegrityResult(
        ok=False,
        status=MediaIntegrityStatus.HASH_MISMATCH,
        reason="hash mismatch",
        content_hash="abc",
        missing_artifacts=("video_hash",),
    )
    assert (
        media_integrity.video_integrity_failure_allows_existing_video_reprocessing(
            result
        )
        is False
    )


def test_check_video_media_integrity_reports_state_not_validated() -> None:
    with (
        patch.object(media_integrity, "file_exists", _always_exists),
        patch.object(media_integrity, "field_file_is_readable", _always_true),
    ):
        result = check_video_media_integrity(
            _fake_video(
                pk=1,
                video_hash="hash-001",
                raw_file_name="raw.bin",
                processed_file_name="processed.bin",
                state_validated=False,
            ),
            content_hash="hash-001",
        )
    assert result.ok is False
    assert result.status == MediaIntegrityStatus.STATE_NOT_VALIDATED
    assert result.media_pk == 1


def test_check_video_media_integrity_preanonymized_does_not_require_raw_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_integrity, "file_exists", _always_exists)
    monkeypatch.setattr(media_integrity, "field_file_is_readable", _always_true)

    result = check_video_media_integrity(
        _fake_video(
            pk=2,
            video_hash="hash-002",
            raw_file_name=None,
            processed_file_name="processed.bin",
            state_validated=True,
        ),
        expectation=MediaIntegrityExpectation.PREANONYMIZED_VIDEO,
        content_hash="hash-002",
    )

    assert result.ok is True
    assert result.status == MediaIntegrityStatus.OK


def test_check_report_media_integrity_reports_missing_hash() -> None:
    result = check_report_media_integrity(
        cast(RawPdfFile | None, None),
        content_hash="   ",
    )

    assert result.ok is False
    assert result.status == MediaIntegrityStatus.MISSING_CONTENT_HASH
    assert result.content_hash == ""


def test_expectation_for_upload_job_uses_upload_tier_and_variants() -> None:
    # pyright: ignore[reportPrivateUsage]
    assert (
        media_integrity._expectation_for_upload_job(
            cast(
                UploadJob,
                _FakeUploadJob(
                    content_hash="hash",
                    content_type="video/mp4",
                    source_system="watcher",
                    storage_tier="upload_preanonymized",
                ),
            )
        )
        == MediaIntegrityExpectation.PREANONYMIZED_VIDEO
    )
    # pyright: ignore[reportPrivateUsage]
    assert (
        media_integrity._expectation_for_upload_job(
            cast(
                UploadJob,
                _FakeUploadJob(
                    content_hash="hash",
                    content_type="video/mp4",
                    source_system="watcher",
                    storage_tier="upload_raw",
                    processing_provenance={"ingest_variant": "preanonymized"},
                ),
            )
        )
        == MediaIntegrityExpectation.PREANONYMIZED_VIDEO
    )
    # pyright: ignore[reportPrivateUsage]
    assert (
        media_integrity._expectation_for_upload_job(
            cast(
                UploadJob,
                _FakeUploadJob(
                    content_hash="hash",
                    content_type="video/mp4",
                    source_system="watcher_preanonymized",
                    storage_tier="upload_raw",
                ),
            )
        )
        == MediaIntegrityExpectation.PREANONYMIZED_VIDEO
    )
    # pyright: ignore[reportPrivateUsage]
    assert (
        media_integrity._expectation_for_upload_job(
            cast(
                UploadJob,
                _FakeUploadJob(
                    content_hash="hash",
                    content_type="video/mp4",
                    source_system="watcher",
                    storage_tier="upload_raw",
                ),
            )
        )
        == MediaIntegrityExpectation.RAW_WATCHER_VIDEO
    )


def test_check_upload_job_media_integrity_reports_unsupported_content_type() -> None:
    result = check_upload_job_media_integrity(
        cast(
            UploadJob,
            _FakeUploadJob(
                content_hash="abc",
                content_type="image/tiff",
                source_system="watcher",
                storage_tier="upload_raw",
            ),
        )
    )
    assert result.status == MediaIntegrityStatus.UNSUPPORTED_CONTENT_TYPE
    assert result.ok is False


def test_check_upload_job_media_integrity_dispatches_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        media_integrity,
        "RawPdfFile",
        SimpleNamespace(
            objects=_FakeQuery(
                cast(
                    RawPdfFile,
                    SimpleNamespace(
                        pk=7,
                        pdf_hash="report-1",
                        processed_file=_FakeFieldFile("report.pdf"),
                        state=SimpleNamespace(anonymization_validated=True),
                    ),
                )
            )
        ),
    )
    monkeypatch.setattr(media_integrity, "file_exists", _always_exists)
    monkeypatch.setattr(media_integrity, "field_file_is_readable", _always_true)

    result = check_upload_job_media_integrity(
        cast(
            UploadJob,
            _FakeUploadJob(
                content_hash="report-1",
                content_type="application/pdf",
                source_system="ui",
                storage_tier="upload_raw",
            ),
        )
    )
    assert result.ok is True
    assert result.status == MediaIntegrityStatus.OK


def test_check_upload_job_media_integrity_dispatches_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        media_integrity,
        "VideoFile",
        SimpleNamespace(
            objects=_FakeQuery(
                _fake_video(
                    pk=11,
                    video_hash="video-1",
                    raw_file_name="raw.mp4",
                    processed_file_name="processed.mp4",
                    state_validated=True,
                )
            )
        ),
    )
    monkeypatch.setattr(media_integrity, "file_exists", _always_exists)
    monkeypatch.setattr(media_integrity, "field_file_is_readable", _always_true)

    result = check_upload_job_media_integrity(
        cast(
            UploadJob,
            _FakeUploadJob(
                content_hash="video-1",
                content_type="video/mp4",
                source_system="watcher",
                storage_tier="upload_raw",
            ),
        )
    )
    assert result.ok is True
    assert result.media_pk == 11
