from contextlib import contextmanager
from collections.abc import Generator
from types import SimpleNamespace
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from endoreg_db.services import video_dimension_backfill as service
from endoreg_db.models.media.video.video_file import VideoFile
from lx_dtypes.models.contracts.video_file import VideoFilePayload
from lx_dtypes.models.contracts.endoscopy_processor import (
    EndoscopeImageRoiCore,
    MaskCallPayload,
    RoiBoxCore,
)


def _roi_box(
    *,
    x: int = 550,
    y: int = 0,
    width: int = 1350,
    height: int = 1080,
) -> RoiBoxCore:
    return RoiBoxCore(x=x, y=y, width=width, height=height)


def _image_roi(
    *,
    x: int = 550,
    y: int = 0,
    width: int = 1350,
    height: int = 1080,
    image_width: int = 1920,
    image_height: int = 1080,
) -> EndoscopeImageRoiCore:
    return EndoscopeImageRoiCore(
        x=x,
        y=y,
        width=width,
        height=height,
        image_width=image_width,
        image_height=image_height,
    )


class _Processor:
    def get_roi_endoscope_image(self) -> EndoscopeImageRoiCore:
        return _image_roi()


class _FieldStorage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self, name: str) -> bool:
        return self.path.exists()

    def open(self, name: str, mode: str = "rb") -> BinaryIO:
        return cast(BinaryIO, self.path.open(mode))

    def save(self, name: str, content: BinaryIO) -> str:
        with self.path.open("wb") as destination:
            destination.write(content.read())
        return name

    def delete(self, name: str) -> None:
        self.path.unlink(missing_ok=True)


class _FieldFile:
    def __init__(self, path: Path, name: str) -> None:
        self.path = str(path)
        self.name = name
        self.storage = _FieldStorage(path)
        self.field = SimpleNamespace(name=name.rsplit("/", maxsplit=1)[-1])
        self.instance: object | None = None

    def delete(self, *, save: bool = False) -> None:
        self.storage.delete(self.name)
        self.name = ""


class _Video:
    pk: int = 123
    id: int = 123
    processor: _Processor = _Processor()
    video_hash: str = "dimension-backfill-video"
    original_file_name: str = "dimension-backfill-video.mp4"
    processed_video_hash: str = ""
    fps: float | None = None
    duration: float | None = None
    frame_count: int | None = None
    width: int | None = None
    height: int | None = None
    storage_mode: str = ""
    raw_streamable_relative_path: str = ""
    processed_streamable_relative_path: str = ""

    def __init__(self, raw_path: Path, processed_path: Path) -> None:
        self._raw_path = raw_path
        self._processed_path = processed_path
        self.raw_file = _FieldFile(raw_path, "raw/raw.mp4")
        self.processed_file = _FieldFile(processed_path, "processed/processed.mp4")
        self.raw_file.instance = self
        self.processed_file.instance = self
        self.saved_update_fields: list[str] = []
        self.contract = VideoFilePayload(
            pk=self.pk,
            id=self.id,
            video_hash=self.video_hash,
            original_file_name=self.original_file_name,
            fps=self.fps,
            duration=self.duration,
            frame_count=self.frame_count,
            width=self.width,
            height=self.height,
            storage_mode=self.storage_mode,
            raw_streamable_relative_path=self.raw_streamable_relative_path,
            processed_streamable_relative_path=self.processed_streamable_relative_path,
            has_raw=self.has_raw,
            is_processed=self.is_processed,
        )

    @property
    def has_raw(self) -> bool:
        return self._raw_path.exists()

    @property
    def is_processed(self) -> bool:
        return self._processed_path.exists()

    def get_raw_file_path(self) -> Path:
        return self._raw_path

    def get_processed_file_path(self) -> Path:
        return self._processed_path

    @contextmanager
    def ensure_local_raw_file(self) -> Generator[Path]:
        yield self._raw_path

    @contextmanager
    def ensure_local_processed_file(self) -> Generator[Path]:
        yield self._processed_path

    def save(self, *, update_fields: list[str]) -> None:
        self.saved_update_fields = update_fields


class _MaskApplication:
    default_mask_config: RoiBoxCore = _roi_box()

    def __init__(self) -> None:
        self.calls: list[MaskCallPayload] = []

    def create_mask_config_from_roi(self, roi: RoiBoxCore) -> RoiBoxCore:
        return _roi_box(
            x=roi.x,
            y=roi.y,
            width=roi.width,
            height=roi.height,
        )

    def mask_video_streaming(self, **kwargs: object) -> bool:
        payload = MaskCallPayload.model_validate(kwargs)
        self.calls.append(payload)
        payload.output_video.write_bytes(b"repaired")
        return True


class _OldMaskApplication:
    default_mask_config: RoiBoxCore = _MaskApplication.default_mask_config

    def create_mask_config_from_roi(self, roi: RoiBoxCore) -> RoiBoxCore:
        return _roi_box(
            x=roi.x,
            y=roi.y,
            width=roi.width,
            height=roi.height,
        )

    def mask_video_streaming(
        self,
        input_video: Path,
        mask_config: RoiBoxCore,
        output_video: Path,
    ) -> bool:
        output_video.write_bytes(b"old")
        return True


def test_backfill_fixes_cropped_processed_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")
    video = _Video(raw_path, processed_path)
    mask_application = _MaskApplication()

    def fake_detect(path: Path) -> EndoscopeImageRoiCore:
        if path == raw_path:
            return _image_roi(
                width=1920, height=1080, image_width=1920, image_height=1080
            )
        if path == processed_path:
            return _image_roi(
                width=1350, height=1080, image_width=1350, image_height=1080
            )
        return _image_roi(width=1920, height=1080, image_width=1920, image_height=1080)

    def fake_sha256_file(path: Path) -> str:
        return "new-hash"

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)
    monkeypatch.setattr(service, "sha256_file", fake_sha256_file)

    result = service.backfill_video_anonymized_dimensions(
        cast(VideoFile, video), mask_application=mask_application
    )

    assert result.status == "repaired"
    assert result.repaired is True
    assert processed_path.read_bytes() == b"repaired"
    assert video.processed_video_hash == "new-hash"
    assert video.saved_update_fields == ["processed_video_hash", "date_modified"]
    assert mask_application.calls[0].mode == service.PRESERVE_DIMENSIONS_MODE
    assert mask_application.calls[0].mask_config.x == 550
    # MaskCallPayload.mask_config is RoiBoxCore; image dimensions live on EndoscopeImageRoiCore before mask config creation.\n    assert video.processor.get_roi_endoscope_image().image_width == 1920


def test_backfill_dry_run_reports_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")
    video = _Video(raw_path, processed_path)
    mask_application = _MaskApplication()

    def fake_detect(path: Path) -> EndoscopeImageRoiCore:
        if path == raw_path:
            return _image_roi(
                width=1920, height=1080, image_width=1920, image_height=1080
            )
        return _image_roi(width=1350, height=1080, image_width=1350, image_height=1080)

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)

    result = service.backfill_video_anonymized_dimensions(
        cast(VideoFile, video),
        dry_run=True,
        mask_application=mask_application,
    )

    assert result.status == "would_repair"
    assert result.repaired is False
    assert processed_path.read_bytes() == b"cropped"
    assert mask_application.calls == []


def test_backfill_refuses_bad_output_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")
    video = _Video(raw_path, processed_path)
    mask_application = _MaskApplication()

    def fake_detect(path: Path) -> EndoscopeImageRoiCore:
        if path == raw_path:
            return _image_roi(
                width=1920, height=1080, image_width=1920, image_height=1080
            )
        if path == processed_path:
            return _image_roi(
                width=1350, height=1080, image_width=1350, image_height=1080
            )
        return _image_roi(width=1350, height=1080, image_width=1350, image_height=1080)

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)

    result = service.backfill_video_anonymized_dimensions(
        cast(VideoFile, video), mask_application=mask_application
    )

    assert result.status == "repair_dimension_mismatch"
    assert result.repaired is False
    assert processed_path.read_bytes() == b"cropped"
    assert not list(tmp_path.glob("*.dimension-backfill.*.mp4"))


def test_backfill_reports_unsupported_lx_anonymizer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    raw_path.write_bytes(b"raw")
    processed_path.write_bytes(b"cropped")

    def fake_detect(path: Path) -> EndoscopeImageRoiCore:
        if path == raw_path:
            return _image_roi(
                width=1920, height=1080, image_width=1920, image_height=1080
            )
        return _image_roi(width=1350, height=1080, image_width=1350, image_height=1080)

    monkeypatch.setattr(service.video_utils, "detect_video_format", fake_detect)

    result = service.backfill_video_anonymized_dimensions(
        cast(VideoFile, _Video(raw_path, processed_path)),
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
def test_backfill_reports_missing_files(
    tmp_path: Path,
    raw_exists: bool,
    processed_exists: bool,
    status: str,
) -> None:
    raw_path = tmp_path / "raw.mp4"
    processed_path = tmp_path / "processed.mp4"
    if raw_exists:
        raw_path.write_bytes(b"raw")
    if processed_exists:
        processed_path.write_bytes(b"processed")

    result = service.backfill_video_anonymized_dimensions(
        cast(VideoFile, _Video(raw_path, processed_path)),
        mask_application=_MaskApplication(),
    )

    assert result.status == status
