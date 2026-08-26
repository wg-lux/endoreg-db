# pyright: reportPrivateUsage=false
import os
import time
import uuid
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Generic, TypeVar

import pytest
from _pytest.monkeypatch import MonkeyPatch

from endoreg_db.import_files.context.file_lock import STALE_LOCK_SECONDS
from endoreg_db.services.reconciliation import ReconciliationService

T = TypeVar("T")


class _FakeQuerySet(Generic[T]):
    def __init__(self, items: Sequence[T]) -> None:
        self._items = list(items)

    def filter(self, **kwargs: object) -> "_FakeQuerySet[T]":
        return self

    def exclude(self, **kwargs: object) -> list[T]:
        return list(self._items)


class _FakeManager(Generic[T]):
    def __init__(self, items: Sequence[T]) -> None:
        self._items = list(items)

    def select_related(self, *args: str, **kwargs: object) -> "_FakeManager[T]":
        return self

    def filter(self, **kwargs: object) -> list[T]:
        return list(self._items)


class _DummyRawFile:
    name: str
    storage: object | None

    def __init__(self, name: str, storage: object | None = None) -> None:
        self.name = name
        self.storage = storage


class _DummyVideo:
    video_hash: str
    raw_file: _DummyRawFile
    suffix: str
    saved: list[tuple[str, ...]]

    def __init__(
        self, video_hash: str, raw_file_name: str, suffix: str = ".mp4"
    ) -> None:
        self.video_hash = video_hash
        self.raw_file = _DummyRawFile(raw_file_name)
        self.suffix = suffix
        self.saved = []

    def get_raw_file_path(self) -> None:
        return None

    def save(self, update_fields: Sequence[str] | None = None) -> None:
        if update_fields is None:
            self.saved.append(())
        else:
            self.saved.append(tuple(update_fields))


class _VideoFileRef:
    video_hash: str
    pk: int

    def __init__(self, video_hash: str, pk: int = 11) -> None:
        self.video_hash = video_hash
        self.pk = pk


class _PdfFileRef:
    pdf_hash: str
    pk: int

    def __init__(self, pdf_hash: str, pk: int = 22) -> None:
        self.pdf_hash = pdf_hash
        self.pk = pk


class _DummyVideoState:
    video_file: _VideoFileRef

    def __init__(self, video_hash: str) -> None:
        self.video_file = _VideoFileRef(video_hash)

    def mark_processing_not_started(self) -> None:
        return None


class _DummyPdfState:
    raw_pdf_file: _PdfFileRef

    def __init__(self, pdf_hash: str) -> None:
        self.raw_pdf_file = _PdfFileRef(pdf_hash)

    def mark_processing_not_started(self) -> None:
        return None


class _FakeAtomic:
    def __enter__(self) -> "_FakeAtomic":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


@pytest.mark.unit
def test_reconciliation_retains_local_lock_files_regardless_of_age(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    import_video = tmp_path / "import_video"
    import_report = tmp_path / "import_report"
    import_video.mkdir(parents=True, exist_ok=True)
    import_report.mkdir(parents=True, exist_ok=True)

    stale_lock = import_video / "stale.mp4.lock"
    stale_lock.write_text("lock")
    old = time.time() - (STALE_LOCK_SECONDS + 10)
    os.utime(stale_lock, (old, old))

    fresh_lock = import_report / "fresh.pdf.lock"
    fresh_lock.write_text("lock")

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "import_video": import_video,
            "import_report": import_report,
        },
        raising=True,
    )

    removed = ReconciliationService().clear_stale_lock_files()

    assert removed == 0
    assert stale_lock.exists()
    assert fresh_lock.exists()


@pytest.mark.unit
def test_reconciliation_retains_artifacts_without_attempt_ownership(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    sensitive_dir = tmp_path / "sensitive_videos"
    anonym_dir = tmp_path / "processed_videos_final"
    temp_dir = tmp_path / "temp"
    for path in (sensitive_dir, anonym_dir, temp_dir):
        path.mkdir(parents=True, exist_ok=True)

    orphan_part = sensitive_dir / "abc.part.mp4"
    orphan_part.write_bytes(b"temp")
    orphan_uuid = temp_dir / f"{uuid.uuid4()}.mp4"
    orphan_uuid.write_bytes(b"temp")
    completed_part = anonym_dir / "donehash.part.mp4"
    completed_part.write_bytes(b"keep")
    old = time.time() - (STALE_LOCK_SECONDS + 10)
    for path in (orphan_part, orphan_uuid, completed_part):
        os.utime(path, (old, old))

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "sensitive_video": sensitive_dir,
            "anonym_video": anonym_dir,
            "transcoding": temp_dir,
        },
        raising=True,
    )
    removed = ReconciliationService().cleanup_orphaned_artifacts()

    assert removed == 0
    assert orphan_part.exists()
    assert orphan_uuid.exists()
    assert completed_part.exists()


@pytest.mark.unit
def test_cleanup_ignores_recent_part_files(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    sensitive_dir = tmp_path / "sensitive_videos"
    anonym_dir = tmp_path / "processed_videos_final"
    temp_dir = tmp_path / "temp"
    for path in (sensitive_dir, anonym_dir, temp_dir):
        path.mkdir(parents=True, exist_ok=True)

    recent_part = sensitive_dir / "recent.part.mp4"
    recent_part.write_bytes(b"fresh")

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "sensitive_video": sensitive_dir,
            "anonym_video": anonym_dir,
            "transcoding": temp_dir,
        },
        raising=True,
    )

    removed = ReconciliationService().cleanup_orphaned_artifacts()

    assert removed == 0
    assert recent_part.exists()


@pytest.mark.unit
def test_cleanup_retains_stale_part_files_without_attempt_ownership(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    sensitive_dir = tmp_path / "sensitive_videos"
    anonym_dir = tmp_path / "processed_videos_final"
    temp_dir = tmp_path / "temp"
    for path in (sensitive_dir, anonym_dir, temp_dir):
        path.mkdir(parents=True, exist_ok=True)

    stale_part = sensitive_dir / "stale.part.mp4"
    stale_part.write_bytes(b"stale")
    old = time.time() - (STALE_LOCK_SECONDS + 10)
    os.utime(stale_part, (old, old))

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "sensitive_video": sensitive_dir,
            "anonym_video": anonym_dir,
            "transcoding": temp_dir,
        },
        raising=True,
    )

    removed = ReconciliationService().cleanup_orphaned_artifacts()

    assert removed == 0
    assert stale_part.exists()


@pytest.mark.unit
def test_startup_reconciliation_delegates_recovery_to_media_integrity(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    calls: list[str] = []

    def fake_clear_stale_lock_files(self: ReconciliationService) -> int:
        calls.append("locks")
        return 0

    def fake_relink_broken_video_raw_files(self: ReconciliationService) -> int:
        calls.append("raw")
        return 0

    def fake_cleanup_orphaned_artifacts(self: ReconciliationService) -> int:
        calls.append("cleanup")
        return 0

    def fake_reset_incomplete_processing_states(self: ReconciliationService) -> int:
        calls.append("states")
        return 0

    def fake_reconcile_media_integrity() -> None:
        calls.append("media_integrity")

    monkeypatch.setattr(
        ReconciliationService,
        "clear_stale_lock_files",
        fake_clear_stale_lock_files,
    )
    monkeypatch.setattr(
        ReconciliationService,
        "relink_broken_video_raw_files",
        fake_relink_broken_video_raw_files,
    )
    monkeypatch.setattr(
        ReconciliationService,
        "cleanup_orphaned_artifacts",
        fake_cleanup_orphaned_artifacts,
    )
    monkeypatch.setattr(
        ReconciliationService,
        "reset_incomplete_processing_states",
        fake_reset_incomplete_processing_states,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "reconcile_media_integrity",
        fake_reconcile_media_integrity,
        raising=True,
    )

    ReconciliationService().run()

    assert calls == ["locks", "raw", "cleanup", "states", "media_integrity"]


@pytest.mark.unit
def test_reconciliation_relinks_broken_raw_file_to_canonical_name(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    legacy_path = sensitive_dir / "legacy_name.mp4"
    legacy_path.write_bytes(b"legacy-video")

    saved: list[tuple[str, ...]] = []

    class DummyVideo(_DummyVideo):
        def __init__(self) -> None:
            super().__init__(
                video_hash="abc123",
                raw_file_name="sensitive_videos/legacy_name.mp4",
            )

        def save(self, update_fields: Sequence[str] | None = None) -> None:
            if update_fields is None:
                saved.append(())
            else:
                saved.append(tuple(update_fields))

    video = DummyVideo()

    class FakeQuerySet(_FakeQuerySet[DummyVideo]):
        def __init__(self) -> None:
            super().__init__([video])

    @contextmanager
    def fake_atomic() -> Generator[None, None, None]:
        yield

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "storage": storage_dir,
            "sensitive_video": sensitive_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "VideoFile",
        SimpleNamespace(objects=FakeQuerySet()),
        raising=True,
    )

    def fake_sha256_file(_path: Path) -> str:
        return "no-match"

    monkeypatch.setattr(
        reconciliation_module,
        "sha256_file",
        fake_sha256_file,
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    canonical_path = sensitive_dir / "abc123.mp4"
    assert recovered == 1
    assert canonical_path.exists()
    assert not legacy_path.exists()
    assert video.raw_file.name == "sensitive_videos/abc123.mp4"
    assert saved == [("raw_file",)]


@pytest.mark.unit
def test_reconciliation_relinks_by_content_hash_for_legacy_overwrite(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    recovered_source = sensitive_dir / "old_import_name.mp4"
    recovered_source.write_bytes(b"legacy-original-bytes")

    class DummyVideo(_DummyVideo):
        def __init__(self) -> None:
            super().__init__(
                video_hash="expected-hash",
                raw_file_name="sensitive_videos/missing.mp4",
            )

        def save(self, update_fields: Sequence[str] | None = None) -> None:
            if update_fields is None:
                self.saved.append(())
            else:
                self.saved.append(tuple(update_fields))

    video = DummyVideo()

    class FakeQuerySet(_FakeQuerySet[DummyVideo]):
        def __init__(self) -> None:
            super().__init__([video])

    @contextmanager
    def fake_atomic() -> Generator[None, None, None]:
        yield

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "storage": storage_dir,
            "sensitive_video": sensitive_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "VideoFile",
        SimpleNamespace(objects=FakeQuerySet()),
        raising=True,
    )

    def fake_sha256_file(path: Path) -> str:
        return "expected-hash" if path == recovered_source else "other"

    monkeypatch.setattr(
        reconciliation_module,
        "sha256_file",
        fake_sha256_file,
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    canonical_path = sensitive_dir / "expected-hash.mp4"
    assert recovered == 1
    assert canonical_path.exists()
    assert not recovered_source.exists()
    assert video.raw_file.name == "sensitive_videos/expected-hash.mp4"


@pytest.mark.unit
def test_reconciliation_skips_relink_when_multiple_content_hash_candidates(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    first = sensitive_dir / "first.mp4"
    second = sensitive_dir / "second.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    class DummyVideo(_DummyVideo):
        def __init__(self) -> None:
            super().__init__(
                video_hash="dupe-hash",
                raw_file_name="sensitive_videos/missing.mp4",
            )

    video = DummyVideo()

    class FakeQuerySet(_FakeQuerySet[DummyVideo]):
        def __init__(self) -> None:
            super().__init__([video])

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "storage": storage_dir,
            "sensitive_video": sensitive_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "VideoFile",
        SimpleNamespace(objects=FakeQuerySet()),
        raising=True,
    )

    def fake_sha256_file(_path: Path) -> str:
        return "dupe-hash"

    monkeypatch.setattr(
        reconciliation_module,
        "sha256_file",
        fake_sha256_file,
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    assert recovered == 0
    assert first.exists()
    assert second.exists()
    assert video.raw_file.name == "sensitive_videos/missing.mp4"


@pytest.mark.unit
def test_relink_skips_if_canonical_taken(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = sensitive_dir / "abc.mp4"
    canonical_path.write_bytes(b"existing")
    lost_path = sensitive_dir / "lost_video.mp4"
    lost_path.write_bytes(b"recover-me")

    class DummyVideo(_DummyVideo):
        def __init__(self) -> None:
            super().__init__(
                video_hash="abc", raw_file_name="sensitive_videos/missing.mp4"
            )

    video = DummyVideo()

    class FakeQuerySet(_FakeQuerySet[DummyVideo]):
        def __init__(self) -> None:
            super().__init__([video])

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "storage": storage_dir,
            "sensitive_video": sensitive_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "VideoFile",
        SimpleNamespace(objects=FakeQuerySet()),
        raising=True,
    )

    def fake_sha256_file(path: Path) -> str:
        return "abc" if path == lost_path else "other"

    monkeypatch.setattr(
        reconciliation_module,
        "sha256_file",
        fake_sha256_file,
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    assert recovered == 0
    assert canonical_path.exists()
    assert lost_path.exists()
    assert video.raw_file.name == "sensitive_videos/missing.mp4"


@pytest.mark.unit
def test_relink_first_winner_for_duplicate_hash(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    legacy_path = sensitive_dir / "legacy.mp4"
    legacy_path.write_bytes(b"recover")

    class DummyVideo(_DummyVideo):
        def __init__(self, label: str) -> None:
            super().__init__(
                video_hash="xyz",
                raw_file_name=f"sensitive_videos/{label}.mp4",
            )

    first_video = DummyVideo("missing_a")
    second_video = DummyVideo("missing_b")

    class FakeQuerySet(_FakeQuerySet[DummyVideo]):
        def __init__(self) -> None:
            super().__init__([first_video, second_video])

    @contextmanager
    def fake_atomic() -> Generator[None, None, None]:
        yield

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "storage": storage_dir,
            "sensitive_video": sensitive_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "VideoFile",
        SimpleNamespace(objects=FakeQuerySet()),
        raising=True,
    )

    def fake_sha256_file(path: Path) -> str:
        return "xyz" if path == legacy_path else "other"

    monkeypatch.setattr(
        reconciliation_module,
        "sha256_file",
        fake_sha256_file,
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    canonical_path = sensitive_dir / "xyz.mp4"
    assert recovered == 1
    assert canonical_path.exists()
    assert first_video.raw_file.name == "sensitive_videos/xyz.mp4"
    assert second_video.raw_file.name == "sensitive_videos/missing_b.mp4"


@pytest.mark.unit
def test_recovery_after_partial_success_updates_db_to_existing_canonical_path(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = sensitive_dir / "crash-hash.mp4"
    canonical_path.write_bytes(b"canonical")

    class DummyVideo(_DummyVideo):
        def __init__(self) -> None:
            super().__init__(
                video_hash="crash-hash",
                raw_file_name="pending/old.mp4",
            )

    video = DummyVideo()

    class FakeQuerySet(_FakeQuerySet[DummyVideo]):
        def __init__(self) -> None:
            super().__init__([video])

    @contextmanager
    def fake_atomic() -> Generator[None, None, None]:
        yield

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "storage": storage_dir,
            "sensitive_video": sensitive_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "VideoFile",
        SimpleNamespace(objects=FakeQuerySet()),
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    assert recovered == 1
    assert canonical_path.exists()
    assert video.raw_file.name == "sensitive_videos/crash-hash.mp4"
    assert video.saved == [("raw_file",)]


@pytest.mark.unit
def test_build_content_hash_index_skips_part_named_candidates_and_hash_errors(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    sensitive_dir = tmp_path / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    good = sensitive_dir / "good.mp4"
    good.write_bytes(b"good")
    skipped_part = sensitive_dir / "ghost.part.mp4"
    skipped_part.write_bytes(b"part")
    bad = sensitive_dir / "bad.mp4"
    bad.write_bytes(b"bad")

    def fake_hash(path: Path) -> str:
        if path == bad:
            raise OSError("broken link")
        if path == good:
            return "target"
        return "other"

    monkeypatch.setattr(reconciliation_module, "sha256_file", fake_hash, raising=True)

    matches = ReconciliationService()._build_content_hash_index(
        sensitive_dir=sensitive_dir,
        target_hashes={"target"},
    )

    assert matches == {"target": [good]}


@pytest.mark.unit
def test_reconciliation_retains_incomplete_states_without_generation_link(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    events: list[tuple[str, str]] = []

    class DummyVideoState(_DummyVideoState):
        def mark_processing_not_started(self) -> None:
            events.append(("video_reset", self.video_file.video_hash))

    class DummyPdfState(_DummyPdfState):
        def mark_processing_not_started(self) -> None:
            events.append(("pdf_reset", self.raw_pdf_file.pdf_hash))

    monkeypatch.setattr(
        reconciliation_module.VideoState,
        "objects",
        _FakeManager([DummyVideoState("video-1")]),
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.RawPdfState,
        "objects",
        _FakeManager([DummyPdfState("pdf-1")]),
        raising=True,
    )
    reset = ReconciliationService().reset_incomplete_processing_states()

    assert reset == 0
    assert events == []


def test_should_run_startup_reconciliation_skips_pytest_entrypoints(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert (
        reconciliation_module.should_run_startup_reconciliation(
            ["/venv/bin/pytest", "-q"]
        )
        is False
    )
    assert (
        reconciliation_module.should_run_startup_reconciliation(
            ["/venv/bin/py.test", "-q"]
        )
        is False
    )


def test_should_run_startup_reconciliation_only_allows_runtime_commands(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.services.reconciliation as reconciliation_module

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert (
        reconciliation_module.should_run_startup_reconciliation(
            ["/venv/bin/python", "runserver"]
        )
        is True
    )
    assert (
        reconciliation_module.should_run_startup_reconciliation(
            ["/venv/bin/python", "load_base_db_data"]
        )
        is False
    )
    assert (
        reconciliation_module.should_run_startup_reconciliation(
            ["/venv/bin/python", "migrate"]
        )
        is False
    )
