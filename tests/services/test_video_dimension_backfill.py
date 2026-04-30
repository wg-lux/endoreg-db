from contextlib import contextmanager
from pathlib import Path

import pytest

from endoreg_db.services import video_dimension_backfill as service


class _Processor:
    def get_roi_endoscope_image(self):
        return {
            "x": 550,
            "y": 0,
            "width": 1350,
            "height": 1080,
            "image_width": 1920,
            "image_height": 1080,
        }


class _Video:
    pk = 123
    processor = _Processor()
    processed_video_hash = ""

    def __init__(self, raw_path: Path, processed_path: Path):
        self._raw_path = raw_path
        self._processed_path = processed_path
        self.saved_update_fields = None

    def get_raw_file_path(self):
        return self._raw_path

    def get_processed_file_path(self):
        return self._processed_path

    @contextmanager
    def ensure_local_raw_file(self):
        yield self._raw_path

    @contextmanager
    def ensure_local_processed_file(self):
        yield self._processed_path

    def save(self, *, update_fields):
        self.saved_update_fields = update_fields


class _MaskApplication:
    default_mask_config = {
        "image_width": 1920,
        "image_height": 1080,
        "endoscope_image_x": 550,
        "endoscope_image_y": 0,
        "endoscope_image_width": 1350,
        "endoscope_image_height": 1080,
    }

    def __init__(self):
        self.calls = []

    def create_mask_config_from_roi(self, roi):
        return {
            "image_width": roi["image_width"],
            "image_height": roi["image_height"],
            "endoscope_image_x": roi["x"],
            "endoscope_image_y": roi["y"],
            "endoscope_image_width": roi["width"],
            "endoscope_image_height": roi["height"],
        }

    def mask_video_streaming(self, **kwargs):
        self.calls.append(kwargs)
        kwargs["output_video"].write_bytes(b"repaired")
        return True


class _OldMaskApplication(_MaskApplication):
    def mask_video_streaming(self, input_video, mask_config, output_video):
        output_video.write_bytes(b"old")
        return True


def test_backfill_fixes_cropped_processed_video(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")
    video = _Video(raw_path, processed_path)
    mask_application = _MaskApplication()

    def fake_detect(path):
        if path == raw_path:
            return {"width": 1920, "height": 1080}
        if path == processed_path:
            return {"width": 1350, "height": 1080}
        return {"width": 1920, "height": 1080}

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)
    monkeypatch.setattr(service, "sha256_file", lambda path: "new-hash")

    result = service.backfill_video_anonymized_dimensions(
        video, mask_application=mask_application
    )

    assert result.status == "repaired"
    assert result.repaired is True
    assert processed_path.read_bytes() == b"repaired"
    assert video.processed_video_hash == "new-hash"
    assert video.saved_update_fields == ["processed_video_hash", "date_modified"]
    assert mask_application.calls[0]["mode"] == service.PRESERVE_DIMENSIONS_MODE
    assert mask_application.calls[0]["mask_config"]["endoscope_image_x"] == 550


def test_backfill_dry_run_reports_without_mutation(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")
    video = _Video(raw_path, processed_path)
    mask_application = _MaskApplication()

    def fake_detect(path):
        if path == raw_path:
            return {"width": 1920, "height": 1080}
        return {"width": 1350, "height": 1080}

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)

    result = service.backfill_video_anonymized_dimensions(
        video,
        dry_run=True,
        mask_application=mask_application,
    )

    assert result.status == "would_repair"
    assert result.repaired is False
    assert processed_path.read_bytes() == b"cropped"
    assert mask_application.calls == []


def test_backfill_refuses_bad_output_dimensions(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")
    video = _Video(raw_path, processed_path)
    mask_application = _MaskApplication()

    def fake_detect(path):
        if path == raw_path:
            return {"width": 1920, "height": 1080}
        if path == processed_path:
            return {"width": 1350, "height": 1080}
        return {"width": 1350, "height": 1080}

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)

    result = service.backfill_video_anonymized_dimensions(
        video, mask_application=mask_application
    )

    assert result.status == "repair_dimension_mismatch"
    assert result.repaired is False
    assert processed_path.read_bytes() == b"cropped"
    assert not list(tmp_path.glob("*.dimension-backfill.*.mp4"))


def test_backfill_reports_unsupported_lx_anonymizer(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")

    def fake_detect(path):
        if path == raw_path:
            return {"width": 1920, "height": 1080}
        return {"width": 1350, "height": 1080}

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)

    result = service.backfill_video_anonymized_dimensions(
        _Video(raw_path, processed_path),
        mask_application=_OldMaskApplication(),
    )

    assert result.status == "unsupported_lx_anonymizer"
    assert processed_path.read_bytes() == b"cropped"


@pytest.mark.parametrize(
    "raw_exists,processed_exists,status",
    [
        (False, True, "missing_source"),
        (True, False, "missing_processed"),
    ],
)
def test_backfill_reports_missing_files(tmp_path, raw_exists, processed_exists, status):
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    if raw_exists:
        raw_path.write_bytes(b"raw")
    if processed_exists:
        processed_path.write_bytes(b"processed")

    result = service.backfill_video_anonymized_dimensions(
        _Video(raw_path, processed_path),
        mask_application=_MaskApplication(),
    )

    assert result.status == status
