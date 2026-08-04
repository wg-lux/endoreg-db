from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


@contextmanager
def _context_path(path: Path):
    yield path


def test_async_reimport_uses_in_place_reanonymization(monkeypatch, tmp_path):
    import endoreg_db.services.jobs.video_reimport_jobs as module

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    events = []
    service_calls = []

    class _FakeVideo:
        pk = 1
        video_hash = "video-hash"
        raw_file = SimpleNamespace(name="raw.mp4")
        center = SimpleNamespace(name="university_hospital_wuerzburg")
        processor = SimpleNamespace(name="olympus_cv_1500")
        video_meta = SimpleNamespace(processor=processor)
        processed_file = SimpleNamespace(name="processed_videos_final/video.mp4")

        def refresh_from_db(self):
            events.append(("refresh_from_db", self))

    video = _FakeVideo()

    class _FakeVideoManager:
        def select_related(self, *args):
            events.append(("select_related", args))
            return self

        def get(self, pk):
            assert pk == 1
            return video

    class _FakeVideoModel:
        objects = _FakeVideoManager()

    class _FakeService:
        def reanonymize_existing_video(self, target_video, *, source_path=None):
            service_calls.append(
                {"target_video": target_video, "source_path": source_path}
            )
            return target_video

        def import_and_anonymize(self, **kwargs):
            raise AssertionError("async reimport should not use full import")

    @contextmanager
    def _fake_atomic():
        yield

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(
        module,
        "ensure_local_file",
        lambda field_file: _context_path(raw_path),
        raising=True,
    )
    monkeypatch.setattr(module.transaction, "atomic", _fake_atomic, raising=True)
    monkeypatch.setattr(
        module,
        "_reset_reimport_state",
        lambda target_video: events.append(("reset", target_video)) or 1,
        raising=True,
    )
    monkeypatch.setattr(
        module,
        "_mark_upload_jobs_anonymized",
        lambda target_video: events.append(("mark_anonymized", target_video)) or 1,
        raising=True,
    )
    monkeypatch.setattr(
        module,
        "_run_prediction_refresh",
        lambda *, video, config: {"status": "skipped", "queued": False},
        raising=True,
    )
    monkeypatch.setattr(module, "VideoImportService", lambda: _FakeService())

    assert module._run_video_reimport_job(1) is True

    assert ("reset", video) in events
    assert service_calls == [{"target_video": video, "source_path": raw_path}]
    assert ("mark_anonymized", video) in events
