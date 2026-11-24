from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false

from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from lx_dtypes.models.contracts.endoscopy_processor import RoiBoxCore
from numpy.typing import NDArray

from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files._ai import (
    _extract_text_from_video_frames,
    _extract_video_metadata_from_frame,
    _video_metadata_ocr_rois,
)
from endoreg_db.utils import frame_stream
from endoreg_db.services.video_files import frame_coordinates


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
@pytest.mark.parametrize(
    "timestamps,cap,selected",
    [
        ((2.0, 2.11, 2.16), 3, [0, 1, 2]),
        ((2.0, 2.04, 2.11, 2.16, 2.24, 2.5, 2.9, 3.0), 3, [0, 2, 4]),
    ],
)
def test_sampled_video_metadata_uses_one_lx_anonymizer_ocr_instance(
    monkeypatch: pytest.MonkeyPatch,
    timestamps: tuple[float, ...],
    cap: int,
    selected: list[int],
) -> None:
    import lx_anonymizer.ocr.ocr_frame as lx_ocr_frame

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

    def fake_read(
        video: VideoFile, *, frame_number: int, file_type: str = "raw"
    ) -> frame_stream.FrameSample:
        assert file_type == "raw"
        return frame_stream.FrameSample(
            frame_number,
            timestamps[frame_number],
            np.full((4, 4, 3), frame_number, dtype=np.uint8),
        )

    monkeypatch.setattr(
        lx_ocr_frame,
        "FrameOCR",
        _FakeFrameOCR,
        raising=True,
    )
    monkeypatch.setattr(frame_stream, "read_video_file_frame_sample", fake_read)

    def canonical_timestamps(*args: object, **kwargs: object) -> tuple[float, ...]:
        return timestamps

    monkeypatch.setattr(
        frame_coordinates, "artifact_frame_timestamps", canonical_timestamps
    )
    video = cast(
        VideoFile,
        SimpleNamespace(
            raw_video_hash="video-hash",
            processor=_Processor(),
        ),
    )

    result = _extract_text_from_video_frames(video, frame_fraction=1.0, cap=cap)

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
    assert sorted({number for number, _region in _FakeFrameOCR.calls}) == selected


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
def test_invalid_frame_fails_before_lx_ocr() -> None:
    class _ForbiddenFrameOCR:
        def extract_text_from_frame(
            self,
            frame: NDArray[np.uint8],
            roi: dict[str, int | None],
            high_quality: bool = True,
        ) -> tuple[str, float, dict[str, object]]:
            raise AssertionError("OCR must not run for an unreadable frame")

    with pytest.raises(ValueError, match="unsigned-byte RGB frame"):
        _extract_video_metadata_from_frame(
            np.zeros((4, 4), dtype=np.uint8),
            {"patient_first_name": {"x": 0, "y": 0, "width": 5, "height": 5}},
            _ForbiddenFrameOCR(),
        )


@pytest.mark.parametrize("frame_fraction,cap", [(0.0, 3), (float("nan"), 3), (1.0, 0)])
def test_metadata_sampling_rejects_invalid_limits(
    frame_fraction: float, cap: int
) -> None:
    with pytest.raises(ValueError, match="Frame fraction"):
        _extract_text_from_video_frames(cast(VideoFile, object()), frame_fraction, cap)


@pytest.mark.parametrize("failure", ["decode", "changed_timestamp"])
def test_metadata_sampling_never_swallows_coordinate_failures(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    timestamps = (2.0,)

    def canonical_timestamps(*args: object, **kwargs: object) -> tuple[float, ...]:
        return timestamps

    monkeypatch.setattr(
        frame_coordinates, "artifact_frame_timestamps", canonical_timestamps
    )

    def read_sample(*args: object, **kwargs: object) -> frame_stream.FrameSample:
        if failure == "decode":
            raise RuntimeError("authenticated decoder failed")
        return frame_stream.FrameSample(0, 2.1, np.zeros((4, 4, 3), dtype=np.uint8))

    monkeypatch.setattr(frame_stream, "read_video_file_frame_sample", read_sample)
    video = cast(
        VideoFile, SimpleNamespace(raw_video_hash="video", processor=_Processor())
    )
    with pytest.raises(
        (ValueError, RuntimeError), match="canonical timeline|decoder failed"
    ):
        _extract_text_from_video_frames(video, 1.0, 1)
