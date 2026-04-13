from __future__ import annotations

from endoreg_db.services import environment_readiness as readiness


def test_check_environment_readiness_does_not_report_cross_filesystem_warning(
    monkeypatch, tmp_path
):
    protected = tmp_path / "protected"
    storage = protected / "storage"
    io_root = protected / "io"
    watcher_video = io_root / "video_import"
    watcher_report = io_root / "report_import"
    watcher_pre = io_root / "preanonymized_import"
    streamable = storage / "streamable"
    streamable_raw = streamable / "raw"
    streamable_processed = streamable / "processed"
    for directory in (
        protected,
        storage,
        io_root,
        watcher_video,
        watcher_report,
        watcher_pre,
        streamable,
        streamable_raw,
        streamable_processed,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(readiness, "PROTECTED_DATA_ROOT", protected)
    monkeypatch.setattr(readiness, "STORAGE_DIR", storage)
    monkeypatch.setattr(readiness, "IO_DIR", io_root)
    monkeypatch.setattr(readiness, "WATCHER_VIDEO_DROP_DIR", watcher_video)
    monkeypatch.setattr(readiness, "WATCHER_REPORT_DROP_DIR", watcher_report)
    monkeypatch.setattr(readiness, "WATCHER_PREANONYMIZED_DROP_DIR", watcher_pre)
    monkeypatch.setattr(readiness, "STREAMABLE_VIDEO_ROOT", streamable)
    monkeypatch.setattr(readiness, "STREAMABLE_RAW_VIDEO_ROOT", streamable_raw)
    monkeypatch.setattr(
        readiness, "STREAMABLE_PROCESSED_VIDEO_ROOT", streamable_processed
    )

    issues = readiness.check_environment_readiness()

    assert not any(issue.code.startswith("streamable_atomic_move_") for issue in issues)


def test_assert_environment_readiness_raises_on_missing_directory(
    monkeypatch, tmp_path
):
    protected = tmp_path / "protected"
    protected.mkdir()

    monkeypatch.setattr(readiness, "PROTECTED_DATA_ROOT", protected)
    monkeypatch.setattr(readiness, "STORAGE_DIR", tmp_path / "missing-storage")
    monkeypatch.setattr(readiness, "IO_DIR", tmp_path / "missing-io")
    monkeypatch.setattr(readiness, "WATCHER_VIDEO_DROP_DIR", tmp_path / "missing-video")
    monkeypatch.setattr(
        readiness, "WATCHER_REPORT_DROP_DIR", tmp_path / "missing-report"
    )
    monkeypatch.setattr(
        readiness, "WATCHER_PREANONYMIZED_DROP_DIR", tmp_path / "missing-pre"
    )
    monkeypatch.setattr(
        readiness, "STREAMABLE_VIDEO_ROOT", tmp_path / "missing-streamable"
    )
    monkeypatch.setattr(
        readiness, "STREAMABLE_RAW_VIDEO_ROOT", tmp_path / "missing-streamable-raw"
    )
    monkeypatch.setattr(
        readiness,
        "STREAMABLE_PROCESSED_VIDEO_ROOT",
        tmp_path / "missing-streamable-processed",
    )

    try:
        readiness.assert_environment_readiness()
    except RuntimeError as exc:
        assert "storage_root_missing" in str(exc)
    else:
        raise AssertionError("expected readiness assertion to fail")


def test_check_environment_readiness_reports_public_media_mount(monkeypatch, tmp_path):
    protected = tmp_path / "protected"
    storage = protected / "storage"
    io_root = protected / "io"
    watcher_video = io_root / "video_import"
    watcher_report = io_root / "report_import"
    watcher_pre = io_root / "preanonymized_import"
    streamable = storage / "streamable"
    streamable_raw = streamable / "raw"
    streamable_processed = streamable / "processed"
    for directory in (
        protected,
        storage,
        io_root,
        watcher_video,
        watcher_report,
        watcher_pre,
        streamable,
        streamable_raw,
        streamable_processed,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(readiness, "PROTECTED_DATA_ROOT", protected)
    monkeypatch.setattr(readiness, "STORAGE_DIR", storage)
    monkeypatch.setattr(readiness, "IO_DIR", io_root)
    monkeypatch.setattr(readiness, "WATCHER_VIDEO_DROP_DIR", watcher_video)
    monkeypatch.setattr(readiness, "WATCHER_REPORT_DROP_DIR", watcher_report)
    monkeypatch.setattr(readiness, "WATCHER_PREANONYMIZED_DROP_DIR", watcher_pre)
    monkeypatch.setattr(readiness, "STREAMABLE_VIDEO_ROOT", streamable)
    monkeypatch.setattr(readiness, "STREAMABLE_RAW_VIDEO_ROOT", streamable_raw)
    monkeypatch.setattr(
        readiness, "STREAMABLE_PROCESSED_VIDEO_ROOT", streamable_processed
    )
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    monkeypatch.setenv("MEDIA_URL", "/media/")
    monkeypatch.setenv("PROTECTED_MEDIA_ROOT", str(storage))

    issues = readiness.check_environment_readiness()

    assert any(issue.code == "media_url_public_mount" for issue in issues)


def test_assert_environment_readiness_raises_when_protected_media_root_escapes_runtime(
    monkeypatch, tmp_path
):
    protected = tmp_path / "protected"
    storage = protected / "storage"
    io_root = protected / "io"
    watcher_video = io_root / "video_import"
    watcher_report = io_root / "report_import"
    watcher_pre = io_root / "preanonymized_import"
    streamable = storage / "streamable"
    streamable_raw = streamable / "raw"
    streamable_processed = streamable / "processed"
    external_media_root = tmp_path / "external-media"
    for directory in (
        protected,
        storage,
        io_root,
        watcher_video,
        watcher_report,
        watcher_pre,
        streamable,
        streamable_raw,
        streamable_processed,
        external_media_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(readiness, "PROTECTED_DATA_ROOT", protected)
    monkeypatch.setattr(readiness, "STORAGE_DIR", storage)
    monkeypatch.setattr(readiness, "IO_DIR", io_root)
    monkeypatch.setattr(readiness, "WATCHER_VIDEO_DROP_DIR", watcher_video)
    monkeypatch.setattr(readiness, "WATCHER_REPORT_DROP_DIR", watcher_report)
    monkeypatch.setattr(readiness, "WATCHER_PREANONYMIZED_DROP_DIR", watcher_pre)
    monkeypatch.setattr(readiness, "STREAMABLE_VIDEO_ROOT", streamable)
    monkeypatch.setattr(readiness, "STREAMABLE_RAW_VIDEO_ROOT", streamable_raw)
    monkeypatch.setattr(
        readiness, "STREAMABLE_PROCESSED_VIDEO_ROOT", streamable_processed
    )
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    monkeypatch.setenv("MEDIA_URL", "/protected_media/")
    monkeypatch.setenv("PROTECTED_MEDIA_ROOT", str(external_media_root))

    try:
        readiness.assert_environment_readiness()
    except RuntimeError as exc:
        assert "protected_media_root_outside_protected_root" in str(exc)
    else:
        raise AssertionError("expected readiness assertion to fail")
