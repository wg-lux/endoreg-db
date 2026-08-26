# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

"""Focused unit tests for the video import orchestration boundaries."""

from __future__ import annotations

import hashlib
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files import video_import_service as sut
from endoreg_db.models.media.video.video_file import VideoFile


def _context(path: Path, **values: object) -> ImportContext:
    data: dict[str, object] = {
        "file_path": path,
        "center_name": "test-center",
        "processor_name": "test-processor",
        "file_type": "video",
    }
    data.update(values)
    return ImportContext.model_validate(data)


class TestVideoAnonymizerDependencyLoading:
    def test_returns_cached_anonymizer_without_loading_dependencies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        class CachedAnonymizer:
            def anonymize_video(self, ctx: ImportContext) -> ImportContext:
                return ctx

        monkeypatch.setattr(sut, "VideoAnonymizer", CachedAnonymizer)

        # Act
        result = sut._load_video_anonymizer_class()

        # Assert
        assert result is CachedAnonymizer

    def test_normalizes_configured_native_capabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        required: list[tuple[str, ...]] = []

        def require_native_capabilities(capabilities: tuple[str, ...]) -> None:
            required.append(capabilities)

        native_module = SimpleNamespace(
            require_native_capabilities=require_native_capabilities
        )
        anonymization_module = ModuleType("video_anonymization")

        class LoadedAnonymizer:
            def anonymize_video(self, ctx: ImportContext) -> ImportContext:
                return ctx

        setattr(anonymization_module, "VideoAnonymizer", LoadedAnonymizer)

        def load_native_module(_name: str) -> object:
            return native_module

        monkeypatch.setattr(sut, "VideoAnonymizer", None)
        monkeypatch.setattr(
            sut,
            "settings",
            SimpleNamespace(LX_ANONYMIZER_REQUIRED_NATIVE_CAPABILITIES="hash, gpu "),
        )
        monkeypatch.setattr(sut, "import_module", load_native_module)
        monkeypatch.setitem(
            sys.modules,
            "endoreg_db.import_files.processing.video_processing.video_anonymization",
            anonymization_module,
        )

        # Act
        result = sut._load_video_anonymizer_class()

        # Assert
        assert result is LoadedAnonymizer
        assert required == [("hash", "gpu")]

    def test_raises_clear_error_when_native_contract_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(sut, "VideoAnonymizer", None)
        monkeypatch.setattr(
            sut,
            "settings",
            SimpleNamespace(LX_ANONYMIZER_REQUIRED_NATIVE_CAPABILITIES=("hash",)),
        )

        def fail_import(_name: str) -> object:
            raise ImportError("missing native module")

        monkeypatch.setattr(sut, "import_module", fail_import)

        # Act / Assert
        with pytest.raises(RuntimeError, match="native capability contract"):
            sut._load_video_anonymizer_class()

    def test_service_constructs_the_anonymizer_lazily(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        constructed: list[bool] = []

        class LoadedAnonymizer:
            def __init__(self) -> None:
                constructed.append(True)

            def anonymize_video(self, ctx: ImportContext) -> ImportContext:
                return ctx

        monkeypatch.setattr(
            sut, "_load_video_anonymizer_class", lambda: LoadedAnonymizer
        )
        service = sut.VideoImportService()

        # Act
        first = service.anonymizer
        second = service.anonymizer

        # Assert
        assert first is second
        assert constructed == [True]


class TestLocalRawSourceResolution:
    def test_prefers_an_instance_provider(self, tmp_path: Path) -> None:
        # Arrange
        raw_path = tmp_path / "raw.mp4"
        video = SimpleNamespace(
            ensure_local_raw_file=lambda: nullcontext(raw_path),
        )

        # Act
        with sut._local_raw_source_context(video) as result:
            resolved = result

        # Assert
        assert resolved == raw_path

    def test_uses_a_class_provider(self, tmp_path: Path) -> None:
        # Arrange
        raw_path = tmp_path / "raw.mp4"

        class VideoLike:
            def ensure_local_raw_file(self):  # type: ignore[no-untyped-def]
                return nullcontext(raw_path)

        # Act
        with sut._local_raw_source_context(VideoLike()) as result:
            resolved = result

        # Assert
        assert resolved == raw_path

    def test_uses_managed_storage_for_a_video_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        raw_path = tmp_path / "managed.mp4"
        video = VideoFile(video_hash="managed-video")
        monkeypatch.setattr(
            sut,
            "ensure_local_raw_video_file",
            lambda _video: nullcontext(raw_path),
        )

        # Act
        with sut._local_raw_source_context(video) as result:
            resolved = result

        # Assert
        assert resolved == raw_path

    def test_uses_fallback_when_reported_raw_path_is_missing(
        self, tmp_path: Path
    ) -> None:
        # Arrange
        missing_path = tmp_path / "missing.mp4"
        fallback_path = tmp_path / "fallback.mp4"
        video = SimpleNamespace(get_raw_file_path=lambda: missing_path)

        # Act
        with sut._local_raw_source_context(
            video, fallback_path=fallback_path
        ) as result:
            resolved = result

        # Assert
        assert resolved == fallback_path

    def test_raises_when_no_raw_source_or_fallback_exists(self) -> None:
        # Arrange
        video = SimpleNamespace(video_hash="abc123")

        # Act / Assert
        with pytest.raises(ValueError, match="Video abc123 has no local raw source"):
            sut._local_raw_source_context(video)


class TestRawSourceValidation:
    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ({"video_meta_id": 1}, True),
            ({"center_id": 1, "processor_id": 2}, True),
            ({"center_id": 1, "processor_id": None}, False),
        ],
    )
    def test_detects_reanonymization_metadata_readiness(
        self, values: dict[str, int | None], expected: bool
    ) -> None:
        # Arrange
        video = VideoFile(**values)

        # Act
        result = sut._supports_reanonymization_metadata_initialization(video)

        # Assert
        assert result is expected

    @pytest.mark.parametrize(
        ("video", "expected"),
        [
            (None, {}),
            (
                SimpleNamespace(
                    width=1920,
                    height=1080,
                    fps=25,
                    duration=12.5,
                    frame_count=313,
                ),
                {
                    "width": 1920,
                    "height": 1080,
                    "fps": 25.0,
                    "duration": 12.5,
                    "frame_count": 313,
                },
            ),
            (
                SimpleNamespace(
                    width=True,
                    height="1080",
                    fps=False,
                    duration=None,
                    frame_count=2.5,
                ),
                {},
            ),
        ],
    )
    def test_builds_only_a_typed_stream_contract(
        self, video: object | None, expected: dict[str, int | float]
    ) -> None:
        # Arrange is supplied by the parameter set.

        # Act
        result = sut._video_meta_stream_contract(cast(VideoFile | None, video))

        # Assert
        assert result == expected

    @pytest.mark.parametrize("source_kind", ["missing", "directory", "empty"])
    def test_rejects_an_unusable_source(self, source_kind: str, tmp_path: Path) -> None:
        # Arrange
        source_path = tmp_path / f"{source_kind}.mp4"
        if source_kind == "directory":
            source_path.mkdir()
        elif source_kind == "empty":
            source_path.touch()
        ctx = _context(tmp_path / "input.mp4")
        identity = sut._RawSourceIdentity(0, 1, "0" * 64)

        # Act / Assert
        expected_error = FileNotFoundError if source_kind == "missing" else RuntimeError
        with pytest.raises(expected_error):
            sut._record_validated_raw_source(ctx, source_path, identity)

    def test_records_identity_and_stream_metadata(self, tmp_path: Path) -> None:
        # Arrange
        source_path = tmp_path / "raw.mp4"
        source_path.write_bytes(b"raw-video")
        ctx = _context(tmp_path / "input.mp4")
        ctx.current_video = cast(
            VideoFile,
            SimpleNamespace(width=1280, height=720, fps=50, duration=1.0),
        )
        identity = sut._RawSourceIdentity(9, 123, "a" * 64)

        # Act
        sut._record_validated_raw_source(ctx, source_path, identity)

        # Assert
        assert ctx.validated_raw_source_sha256 == "a" * 64
        assert ctx.validated_raw_source_stream == {
            "width": 1280,
            "height": 720,
            "fps": 50.0,
            "duration": 1.0,
        }

    def test_uses_native_file_identity_when_available(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        source_path = tmp_path / "raw.mp4"
        native_identity = (7, 123, "b" * 64)

        def stable_identity(_path: Path) -> tuple[int, int, str]:
            return native_identity

        monkeypatch.setattr(sut, "stable_file_identity", stable_identity)

        # Act
        result = sut._raw_source_identity(source_path)

        # Assert
        assert result == sut._RawSourceIdentity(*native_identity)

    def test_hashes_a_stable_source_with_the_real_hash_helper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        source_path = tmp_path / "raw.mp4"
        source_path.write_bytes(b"stable-video")

        def no_native_identity(_path: Path) -> None:
            return None

        monkeypatch.setattr(sut, "stable_file_identity", no_native_identity)

        # Act
        result = sut._raw_source_identity(source_path)

        # Assert
        assert result.size_bytes == len(b"stable-video")
        assert result.sha256 == hashlib.sha256(b"stable-video").hexdigest()


class TestQualityModeConfiguration:
    def test_uses_environment_default_when_django_setting_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(sut, "settings", SimpleNamespace())
        monkeypatch.setattr(
            sut, "get_ffmpeg_transcode_quality_mode", lambda: "balanced"
        )

        # Act
        result = sut._configured_reimport_transcode_quality_mode()

        # Assert
        assert result == "balanced"

    def test_normalizes_a_supported_django_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        configured_mode = next(iter(sut.FFMPEG_TRANSCODE_QUALITY_MODES))
        monkeypatch.setattr(
            sut,
            "settings",
            SimpleNamespace(
                FFMPEG_TRANSCODE_QUALITY_MODE=f" {configured_mode.upper()} "
            ),
        )

        # Act
        result = sut._configured_reimport_transcode_quality_mode()

        # Assert
        assert result == configured_mode

    def test_rejects_an_unsupported_django_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(
            sut,
            "settings",
            SimpleNamespace(FFMPEG_TRANSCODE_QUALITY_MODE="unbounded"),
        )

        # Act / Assert
        with pytest.raises(ValueError, match="must be one of"):
            sut._configured_reimport_transcode_quality_mode()

    def test_uses_environment_default_when_django_settings_are_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        class UnconfiguredSettings:
            def __getattr__(self, _name: str) -> object:
                raise sut.ImproperlyConfigured("settings unavailable")

        monkeypatch.setattr(sut, "settings", UnconfiguredSettings())
        monkeypatch.setattr(
            sut, "get_ffmpeg_transcode_quality_mode", lambda: "balanced"
        )

        # Act
        result = sut._configured_reimport_transcode_quality_mode()

        # Assert
        assert result == "balanced"


class TestFailureOwnership:
    @pytest.mark.parametrize("preserve_existing", [False, True])
    def test_finalizes_only_after_execution_guard_succeeds(
        self,
        preserve_existing: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        events: list[object] = []
        ctx = _context(
            tmp_path / "input.mp4", execution_guard=lambda: events.append("guard")
        )

        def finalize(_ctx: ImportContext, **kwargs: object) -> None:
            events.append(kwargs)

        monkeypatch.setattr(sut, "finalize_failure", finalize)

        # Act
        sut._finalize_video_failure_if_owned(
            ctx, preserve_existing_video_artifacts=preserve_existing
        )

        # Assert
        expected_kwargs = (
            {"preserve_existing_video_artifacts": True} if preserve_existing else {}
        )
        assert events == ["guard", expected_kwargs]

    def test_does_not_finalize_after_execution_guard_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        finalized = False

        def reject() -> None:
            raise RuntimeError("lease lost")

        def finalize(_ctx: ImportContext) -> None:
            nonlocal finalized
            finalized = True

        ctx = _context(tmp_path / "input.mp4", execution_guard=reject)
        monkeypatch.setattr(sut, "finalize_failure", finalize)

        # Act / Assert
        with pytest.raises(RuntimeError, match="lease lost"):
            sut._finalize_video_failure_if_owned(ctx)
        assert finalized is False


class TestNormalizationPreconditions:
    @pytest.mark.parametrize("anonymized_value", [None, ""])
    def test_requires_an_anonymized_output_path(
        self, anonymized_value: object, tmp_path: Path
    ) -> None:
        # Arrange
        ctx = _context(tmp_path / "input.mp4")
        ctx.anonymized_path = cast(Path | None, anonymized_value)

        # Act / Assert
        with pytest.raises(RuntimeError, match="without an anonymized video output"):
            sut._normalize_reimport_video_quality(ctx)

    def test_rejects_an_empty_anonymized_output(self, tmp_path: Path) -> None:
        # Arrange
        output_path = tmp_path / "output.mp4"
        output_path.touch()
        ctx = _context(tmp_path / "input.mp4", anonymized_path=output_path)

        # Act / Assert
        with pytest.raises(RuntimeError, match="missing or empty"):
            sut._normalize_reimport_video_quality(ctx)

    def test_requires_a_validated_raw_reference(self, tmp_path: Path) -> None:
        # Arrange
        output_path = tmp_path / "output.mp4"
        output_path.write_bytes(b"output")
        ctx = _context(tmp_path / "input.mp4", anonymized_path=output_path)

        # Act / Assert
        with pytest.raises(RuntimeError, match="validated raw source"):
            sut._normalize_reimport_video_quality(ctx)


class TestImportInputBoundaries:
    def test_rejects_null_file_path_before_touching_dependencies(self) -> None:
        # Arrange
        service = sut.VideoImportService()

        # Act / Assert
        with pytest.raises(TypeError):
            service.import_and_anonymize(
                cast(str, None), "test-center", "test-processor"
            )

    @pytest.mark.parametrize("field", ["center_name", "processor_name"])
    def test_rejects_empty_required_names(self, field: str, tmp_path: Path) -> None:
        # Arrange
        source_path = tmp_path / "input.mp4"
        service = sut.VideoImportService()

        # Act / Assert
        with pytest.raises(ValidationError, match=field):
            if field == "center_name":
                service.import_and_anonymize(source_path, "  ", "test-processor")
            else:
                service.import_and_anonymize(source_path, "test-center", "  ")

    def test_fenced_entrypoint_forwards_one_complete_execution_capability(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        source_path = tmp_path / "input.mp4"
        source_path.write_bytes(b"video")
        attempt_id = "a" * 32
        observed_attempts: list[str] = []
        guard_calls: list[str] = []
        existing_video = VideoFile(id=1, video_hash="hash")

        def existing_completed(
            _service: sut.VideoImportService, ctx: ImportContext
        ) -> VideoFile:
            observed_attempts.append(ctx.attempt_id)
            return existing_video

        monkeypatch.setattr(
            sut,
            "_raw_source_identity",
            lambda _path: sut._RawSourceIdentity(5, 1, "hash"),
        )
        monkeypatch.setattr(sut, "file_lock", lambda _path: nullcontext())
        monkeypatch.setattr(
            sut, "content_hash_lock", lambda _hash, _root: nullcontext()
        )
        monkeypatch.setattr(
            sut.VideoImportService,
            "_get_existing_completed_video",
            existing_completed,
        )
        monkeypatch.setattr(sut, "ensure_video_hls", lambda _video: None)
        monkeypatch.setattr(
            sut.VideoImportService,
            "_cleanup_duplicate_staging",
            lambda _service, _ctx: None,
        )
        service = sut.VideoImportService()

        # Act
        result = service.import_and_anonymize_fenced(
            source_path,
            "test-center",
            "test-processor",
            execution_fence=sut.VideoImportExecutionFence(
                attempt_id=attempt_id,
                guard=lambda: guard_calls.append("guard"),
            ),
        )

        # Assert
        assert result is existing_video
        assert observed_attempts == [attempt_id]
        assert guard_calls == ["guard", "guard"]

    def test_execution_fence_rejects_an_empty_attempt_id(self) -> None:
        with pytest.raises(ValueError, match="requires an attempt_id"):
            sut.VideoImportExecutionFence(attempt_id="  ", guard=lambda: None)


class TestStorageBudget:
    @pytest.mark.parametrize(
        ("free_space", "raises"),
        [(25, False), (24, True)],
    )
    def test_enforces_the_exact_pipeline_budget_boundary(
        self,
        free_space: int,
        raises: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(b"x" * 10)

        def disk_usage(_path: Path) -> SimpleNamespace:
            return SimpleNamespace(total=100, used=100 - free_space, free=free_space)

        monkeypatch.setattr(sut.shutil, "disk_usage", disk_usage)
        service = sut.VideoImportService()

        # Act / Assert
        if raises:
            with pytest.raises(sut.InsufficientStorageError) as exc_info:
                service._ensure_pipeline_storage_budget(source_path)
            assert exc_info.value.required_space == 25
        else:
            service._ensure_pipeline_storage_budget(source_path)


class TestExistingCompletedVideoLookup:
    def test_skips_database_lookup_without_a_string_hash(self, tmp_path: Path) -> None:
        # Arrange
        ctx = _context(tmp_path / "input.mp4")
        service = sut.VideoImportService()

        # Act
        result = service._get_existing_completed_video(ctx)

        # Assert
        assert result is None

    def test_treats_missing_video_row_as_failed_integrity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        ctx = _context(tmp_path / "input.mp4", file_hash="content-hash")
        service = sut.VideoImportService()

        def has_history_for_hash(*, file_hash: str, success: bool) -> bool:
            return bool(file_hash and success)

        def missing_video(_file_hash: str) -> VideoFile:
            raise VideoFile.DoesNotExist()

        def failed_integrity(
            video: VideoFile | None, *, content_hash: str
        ) -> SimpleNamespace:
            return SimpleNamespace(
                ok=False,
                reason=f"video row missing for {content_hash}",
                video=video,
            )

        monkeypatch.setattr(
            sut.ProcessingHistory,
            "has_history_for_hash",
            has_history_for_hash,
        )
        monkeypatch.setattr(sut, "get_video_by_content_hash", missing_video)
        monkeypatch.setattr(sut, "check_video_media_integrity", failed_integrity)

        # Act
        result = service._get_existing_completed_video(ctx)

        # Assert
        assert result is None
        assert ctx.current_video is None

    def test_preserves_reprocessable_existing_video_on_integrity_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        ctx = _context(tmp_path / "input.mp4", file_hash="content-hash")
        existing_video = VideoFile(id=7, video_hash="content-hash")
        service = sut.VideoImportService()

        def has_history_for_hash(*, file_hash: str, success: bool) -> bool:
            return bool(file_hash and success)

        monkeypatch.setattr(
            sut.ProcessingHistory,
            "has_history_for_hash",
            has_history_for_hash,
        )
        monkeypatch.setattr(
            sut, "get_video_by_content_hash", lambda _hash: existing_video
        )
        monkeypatch.setattr(
            sut,
            "check_video_media_integrity",
            lambda _video, **_kwargs: SimpleNamespace(
                ok=False, reason="missing output"
            ),
        )
        monkeypatch.setattr(
            sut,
            "video_integrity_failure_allows_existing_video_reprocessing",
            lambda _result: True,
        )

        # Act
        result = service._get_existing_completed_video(ctx)

        # Assert
        assert result is None
        assert ctx.current_video is existing_video


class TestVerifiedLocalRawSource:
    def test_rejects_source_changes_during_metadata_extraction(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        raw_path = tmp_path / "raw.mp4"
        raw_path.write_bytes(b"video")
        video = cast(VideoFile, SimpleNamespace(video_hash="changing-video"))
        ctx = _context(tmp_path / "input.mp4")
        ctx.current_video = video
        identities = iter(
            [
                sut._RawSourceIdentity(5, 1, "a" * 64),
                sut._RawSourceIdentity(6, 2, "b" * 64),
            ]
        )
        monkeypatch.setattr(
            sut,
            "_local_raw_source_context",
            lambda _video, **_kwargs: nullcontext(raw_path),
        )
        monkeypatch.setattr(sut, "_raw_source_identity", lambda _path: next(identities))
        service = sut.VideoImportService()

        # Act / Assert
        with pytest.raises(RuntimeError, match="changed during VideoMeta extraction"):
            with service._verified_local_raw_source(ctx):
                pass


class TestReanonymizationInputBoundaries:
    def test_rejects_an_explicit_missing_source(self, tmp_path: Path) -> None:
        # Arrange
        video = VideoFile(video_hash="known-video")
        missing_path = tmp_path / "missing.mp4"
        service = sut.VideoImportService()

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="Video file not found"):
            service.reanonymize_existing_video(video, source_path=missing_path)

    def test_resolves_managed_source_when_no_explicit_path_is_given(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        video = VideoFile(video_hash="known-video")
        missing_path = tmp_path / "missing-managed.mp4"
        monkeypatch.setattr(
            sut,
            "_local_raw_source_context",
            lambda _video: nullcontext(missing_path),
        )
        service = sut.VideoImportService()

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="Video file not found"):
            service.reanonymize_existing_video(video)
