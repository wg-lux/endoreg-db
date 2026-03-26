import os
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from endoreg_db.import_files.context.file_lock import STALE_LOCK_SECONDS
from endoreg_db.services.reconciliation import ReconciliationService


@pytest.mark.unit
def test_reconciliation_removes_stale_lock_files(monkeypatch, tmp_path):
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

    assert removed == 1
    assert not stale_lock.exists()
    assert fresh_lock.exists()


@pytest.mark.unit
def test_reconciliation_cleans_orphaned_temp_and_uuid_files(monkeypatch, tmp_path):
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

    assert removed == 3
    assert not orphan_part.exists()
    assert not orphan_uuid.exists()
    assert not completed_part.exists()


@pytest.mark.unit
def test_cleanup_ignores_recent_part_files(monkeypatch, tmp_path):
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
def test_cleanup_removes_stale_part_files(monkeypatch, tmp_path):
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

    assert removed == 1
    assert not stale_part.exists()


@pytest.mark.unit
def test_reconciliation_relinks_broken_raw_file_to_canonical_name(
    monkeypatch, tmp_path
):
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    legacy_path = sensitive_dir / "legacy_name.mp4"
    legacy_path.write_bytes(b"legacy-video")

    saved = []

    class DummyRawFile:
        def __init__(self, name):
            self.name = name

    class DummyVideo:
        def __init__(self):
            self.video_hash = "abc123"
            self.raw_file = DummyRawFile("sensitive_videos/legacy_name.mp4")
            self.suffix = ".mp4"

        def get_raw_file_path(self):
            return None

        def save(self, update_fields=None):
            saved.append(tuple(update_fields or ()))

    video = DummyVideo()

    class FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def exclude(self, **kwargs):
            return [video]

    @contextmanager
    def fake_atomic():
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
        reconciliation_module,
        "sha256_file",
        lambda path: "no-match",
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
    monkeypatch, tmp_path
):
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    recovered_source = sensitive_dir / "old_import_name.mp4"
    recovered_source.write_bytes(b"legacy-original-bytes")

    class DummyRawFile:
        def __init__(self, name):
            self.name = name

    class DummyVideo:
        def __init__(self):
            self.video_hash = "expected-hash"
            self.raw_file = DummyRawFile("sensitive_videos/missing.mp4")
            self.suffix = ".mp4"
            self.saved = []

        def get_raw_file_path(self):
            return None

        def save(self, update_fields=None):
            self.saved.append(tuple(update_fields or ()))

    video = DummyVideo()

    class FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def exclude(self, **kwargs):
            return [video]

    @contextmanager
    def fake_atomic():
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
        reconciliation_module,
        "sha256_file",
        lambda path: "expected-hash" if path == recovered_source else "other",
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
    monkeypatch, tmp_path
):
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    first = sensitive_dir / "first.mp4"
    second = sensitive_dir / "second.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    class DummyRawFile:
        def __init__(self, name):
            self.name = name

    class DummyVideo:
        def __init__(self):
            self.video_hash = "dupe-hash"
            self.raw_file = DummyRawFile("sensitive_videos/missing.mp4")
            self.suffix = ".mp4"
            self.saved = []

        def get_raw_file_path(self):
            return None

        def save(self, update_fields=None):
            self.saved.append(tuple(update_fields or ()))

    video = DummyVideo()

    class FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def exclude(self, **kwargs):
            return [video]

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
        reconciliation_module,
        "sha256_file",
        lambda path: "dupe-hash",
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    assert recovered == 0
    assert first.exists()
    assert second.exists()
    assert video.raw_file.name == "sensitive_videos/missing.mp4"


@pytest.mark.unit
def test_relink_skips_if_canonical_taken(monkeypatch, tmp_path):
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = sensitive_dir / "abc.mp4"
    canonical_path.write_bytes(b"existing")
    lost_path = sensitive_dir / "lost_video.mp4"
    lost_path.write_bytes(b"recover-me")

    class DummyRawFile:
        def __init__(self, name):
            self.name = name

    class DummyVideo:
        def __init__(self):
            self.video_hash = "abc"
            self.raw_file = DummyRawFile("sensitive_videos/missing.mp4")
            self.suffix = ".mp4"
            self.saved = []

        def get_raw_file_path(self):
            return None

        def save(self, update_fields=None):
            self.saved.append(tuple(update_fields or ()))

    video = DummyVideo()

    class FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def exclude(self, **kwargs):
            return [video]

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
        reconciliation_module,
        "sha256_file",
        lambda path: "abc" if path == lost_path else "other",
        raising=True,
    )

    recovered = ReconciliationService().relink_broken_video_raw_files()

    assert recovered == 0
    assert canonical_path.exists()
    assert lost_path.exists()
    assert video.raw_file.name == "sensitive_videos/missing.mp4"


@pytest.mark.unit
def test_relink_first_winner_for_duplicate_hash(monkeypatch, tmp_path):
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    legacy_path = sensitive_dir / "legacy.mp4"
    legacy_path.write_bytes(b"recover")

    class DummyRawFile:
        def __init__(self, name):
            self.name = name

    class DummyVideo:
        def __init__(self, label):
            self.video_hash = "xyz"
            self.raw_file = DummyRawFile(f"sensitive_videos/{label}.mp4")
            self.suffix = ".mp4"
            self.saved = []

        def get_raw_file_path(self):
            return None

        def save(self, update_fields=None):
            self.saved.append(tuple(update_fields or ()))

    first_video = DummyVideo("missing_a")
    second_video = DummyVideo("missing_b")

    class FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def exclude(self, **kwargs):
            return [first_video, second_video]

    @contextmanager
    def fake_atomic():
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
        reconciliation_module,
        "sha256_file",
        lambda path: "xyz" if path == legacy_path else "other",
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
    monkeypatch, tmp_path
):
    import endoreg_db.services.reconciliation as reconciliation_module

    storage_dir = tmp_path / "storage"
    sensitive_dir = storage_dir / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = sensitive_dir / "crash-hash.mp4"
    canonical_path.write_bytes(b"canonical")

    class DummyRawFile:
        def __init__(self, name):
            self.name = name

    class DummyVideo:
        def __init__(self):
            self.video_hash = "crash-hash"
            self.raw_file = DummyRawFile("pending/old.mp4")
            self.suffix = ".mp4"
            self.saved = []

        def get_raw_file_path(self):
            return None

        def save(self, update_fields=None):
            self.saved.append(tuple(update_fields or ()))

    video = DummyVideo()

    class FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def exclude(self, **kwargs):
            return [video]

    @contextmanager
    def fake_atomic():
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
    monkeypatch, tmp_path
):
    import endoreg_db.services.reconciliation as reconciliation_module

    sensitive_dir = tmp_path / "sensitive_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    good = sensitive_dir / "good.mp4"
    good.write_bytes(b"good")
    skipped_part = sensitive_dir / "ghost.part.mp4"
    skipped_part.write_bytes(b"part")
    bad = sensitive_dir / "bad.mp4"
    bad.write_bytes(b"bad")

    def fake_hash(path):
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
def test_reconciliation_resets_incomplete_processing_states(monkeypatch):
    import endoreg_db.services.reconciliation as reconciliation_module

    events = []

    class DummyVideoState:
        def __init__(self, video_hash):
            self.video_file = SimpleNamespace(video_hash=video_hash, pk=11)

        def mark_processing_not_started(self):
            events.append(("video_reset", self.video_file.video_hash))

    class DummyPdfState:
        def __init__(self, pdf_hash):
            self.raw_pdf_file = SimpleNamespace(pdf_hash=pdf_hash, pk=22)

        def mark_processing_not_started(self):
            events.append(("pdf_reset", self.raw_pdf_file.pdf_hash))

    class FakeManager:
        def __init__(self, items):
            self.items = items

        def select_related(self, *args, **kwargs):
            return self

        def filter(self, **kwargs):
            return self.items

    class FakeAtomic:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        reconciliation_module.VideoState,
        "objects",
        FakeManager([DummyVideoState("video-1")]),
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.RawPdfState,
        "objects",
        FakeManager([DummyPdfState("pdf-1")]),
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.transaction,
        "atomic",
        FakeAtomic,
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module.ProcessingHistory,
        "mark_failure",
        staticmethod(
            lambda **kwargs: events.append(("mark_failure", kwargs["file_hash"]))
        ),
        raising=True,
    )

    reset = ReconciliationService().reset_incomplete_processing_states()

    assert reset == 2
    assert ("video_reset", "video-1") in events
    assert ("pdf_reset", "pdf-1") in events
    assert ("mark_failure", "video-1") in events
    assert ("mark_failure", "pdf-1") in events


def test_should_run_startup_reconciliation_skips_pytest_entrypoints(monkeypatch):
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


def test_should_run_startup_reconciliation_only_allows_runtime_commands(monkeypatch):
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
