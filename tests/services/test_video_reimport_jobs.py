from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch
from pytest import MonkeyPatch

@contextmanager
def _context_path(path: Path) -> Any:
    yield path


def test_async_reimport_uses_in_place_reanonymization(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as module

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    events: list[tuple[str, object]] = []
    service_calls: list[dict[str, object]] = []

    class _FakeVideo:
        pk = 1
        video_hash = "video-hash"
        raw_file = SimpleNamespace(name="raw.mp4")
        center = SimpleNamespace(name="university_hospital_wuerzburg")
        processor = SimpleNamespace(name="olympus_cv_1500")
        video_meta = SimpleNamespace(processor=processor)
        processed_file = SimpleNamespace(name="processed_videos_final/video.mp4")

        def refresh_from_db(self) -> None:
            events.append(("refresh_from_db", self))

    video = _FakeVideo()

    class _FakeVideoManager:
        def select_related(self, *args: str) -> "_FakeVideoManager":
            events.append(("select_related", args))
            return self

        def get(self, pk: int) -> _FakeVideo:
            assert pk == 1
            return video

    class _FakeVideoModel:
        objects = _FakeVideoManager()

    class _FakeService:
        def reanonymize_existing_video(
            self, target_video: object, *, source_path: Path | None = None
        ) -> object:
            service_calls.append(
                {"target_video": target_video, "source_path": source_path}
            )
            return target_video

        def import_and_anonymize(self, **kwargs: Any) -> object:
            raise AssertionError("async reimport should not use full import")

    @contextmanager
    def _fake_atomic() -> Any:
        yield


    def fake_ensure_local_file(field_file: object) -> Any:
        return _context_path(raw_path)

    def fake_reset_reimport_state(target_video: object) -> int:
        events.append(("reset", target_video))
        return 1

    def fake_mark_upload_jobs_anonymized(target_video: object) -> int:
        events.append(("mark_anonymized", target_video))
        return 1

    def fake_run_prediction_refresh(*, video: object, config: object) -> dict[str, object]:
        return {"status": "skipped", "queued": False}

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(module, "ensure_local_file", fake_ensure_local_file, raising=True)
    monkeypatch.setattr(module.transaction, "atomic", _fake_atomic, raising=True)
    monkeypatch.setattr(module, "_reset_reimport_state", fake_reset_reimport_state, raising=True)
    monkeypatch.setattr(module, "_mark_upload_jobs_anonymized", fake_mark_upload_jobs_anonymized, raising=True)
    monkeypatch.setattr(module, "_run_prediction_refresh", fake_run_prediction_refresh, raising=True)
    def fake_video_import_service_factory() -> _FakeService:
        return _FakeService()

    monkeypatch.setattr(module, "VideoImportService", fake_video_import_service_factory)

    assert module._run_video_reimport_job(1) is True  # pyright: ignore[reportPrivateUsage]

    assert ("reset", video) in events
    assert service_calls == [{"target_video": video, "source_path": raw_path}]
    assert ("mark_anonymized", video) in events
