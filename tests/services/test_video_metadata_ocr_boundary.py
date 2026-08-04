from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from lx_dtypes.models.contracts.endoscopy_processor import RoiBoxCore
from numpy.typing import NDArray

from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.media.video.video_file import VideoFile
import endoreg_db.services.video_files._ai as video_ai_module
from endoreg_db.services.video_files._ai import (
    _extract_text_from_video_frames,
    _extract_video_metadata_from_frame,
    _video_metadata_ocr_rois,
)


class _Processor:
    pk = 7

    @staticmethod
    def _roi(x: int) -> RoiBoxCore:
        return RoiBoxCore(x=x, y=0, width=10, height=5)

    def get_roi_examination_date(self) -> RoiBoxCore:
        return self._roi(0)

    def get_roi_patient_first_name(self) -> RoiBoxCore:
        return self._roi(10)

    def get_roi_patient_last_name(self) -> RoiBoxCore:
        return self._roi(20)

    def get_roi_patient_dob(self) -> RoiBoxCore:
        return self._roi(30)

    def get_roi_endoscope_type(self) -> RoiBoxCore | None:
        return self._roi(40)

    def get_roi_endoscopy_sn(self) -> RoiBoxCore | None:
        return self._roi(50)


@pytest.mark.unit
def test_sampled_video_metadata_uses_one_lx_anonymizer_ocr_instance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import lx_anonymizer.ocr.ocr_frame as lx_ocr_frame

    frame_paths = tuple(tmp_path / f"frame-{index}.png" for index in range(3))
    for frame_path in frame_paths:
        frame_path.write_bytes(b"frame")

    class _FakeFrameOCR:
        instances = 0
        calls: list[tuple[int, int]] = []

        def __init__(self) -> None:
            type(self).instances += 1

        def extract_text_from_frame(
            self,
            frame: NDArray[np.uint8],
            roi: dict[str, int | None],
            high_quality: bool = True,
        ) -> tuple[str, float, dict[str, object]]:
            frame_index = int(frame[0, 0])
            x = roi["x"]
            assert isinstance(x, int)
            assert high_quality is True
            type(self).calls.append((frame_index, x))
            first_name = "Alice" if frame_index != 1 else "Bob"
            values_by_x = {
                0: "27.07.2026",
                10: first_name,
                20: "Mustermann",
                30: "04.05.1980",
                40: "GIF  HQ190",
                50: "SN 123",
            }
            return values_by_x[x], 0.9, {}

    def fake_imread(filename: str, _flags: int) -> NDArray[np.uint8]:
        frame_index = int(Path(filename).stem.rsplit("-", maxsplit=1)[1])
        return np.full((4, 4), frame_index, dtype=np.uint8)

    monkeypatch.setattr(
        lx_ocr_frame,
        "FrameOCR",
        _FakeFrameOCR,
        raising=True,
    )
    monkeypatch.setattr(video_ai_module.cv2, "imread", fake_imread, raising=True)
    video = cast(
        VideoFile,
        SimpleNamespace(
            video_hash="video-hash",
            processor=_Processor(),
            get_or_create_state=lambda: SimpleNamespace(frames_extracted=True),
            get_frame_paths=lambda: list(frame_paths),
        ),
    )

    result = _extract_text_from_video_frames(video, frame_fraction=1.0, cap=3)

    assert result == {
        "examination_date": "2026-07-27",
        "patient_first_name": "Alice",
        "patient_last_name": "Mustermann",
        "patient_dob": "1980-05-04",
        "endoscope_type": "GIF HQ190",
        "endoscope_sn": "SN 123",
    }
    assert _FakeFrameOCR.instances == 1
    assert len(_FakeFrameOCR.calls) == 18


@pytest.mark.unit
def test_unconfigured_optional_region_is_not_sent_to_lx_ocr() -> None:
    class _ProcessorWithoutEndoscopeType(_Processor):
        def get_roi_endoscope_type(self) -> None:
            return None

    rois = _video_metadata_ocr_rois(
        cast(EndoscopyProcessor, _ProcessorWithoutEndoscopeType())
    )

    assert "endoscope_type" not in rois
    assert "patient_first_name" in rois


@pytest.mark.unit
def test_unreadable_frame_fails_before_lx_ocr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _ForbiddenFrameOCR:
        def extract_text_from_frame(
            self,
            frame: NDArray[np.uint8],
            roi: dict[str, int | None],
            high_quality: bool = True,
        ) -> tuple[str, float, dict[str, object]]:
            raise AssertionError("OCR must not run for an unreadable frame")

    def unreadable_imread(_filename: str, _flags: int) -> None:
        return None

    monkeypatch.setattr(
        video_ai_module.cv2,
        "imread",
        unreadable_imread,
        raising=True,
    )

    with pytest.raises(ValueError, match="frame is unreadable"):
        _extract_video_metadata_from_frame(
            tmp_path / "unreadable.png",
            {"patient_first_name": {"x": 0, "y": 0, "width": 5, "height": 5}},
            _ForbiddenFrameOCR(),
        )
