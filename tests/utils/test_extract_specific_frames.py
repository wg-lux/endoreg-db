from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.utils import extract_specific_frames


@pytest.mark.unit
def test_selected_frame_extraction_ignores_legacy_fps_and_preserves_indices(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[int, int]] = []

    def fake_extract_frame_range(
        video_path: Path,
        output_dir: Path,
        start_frame: int,
        end_frame: int,
        quality: int,
        ext: str,
    ) -> list[Path]:
        _ = video_path, quality
        calls.append((start_frame, end_frame))
        output = output_dir / f"frame_{start_frame:07d}.{ext}"
        output.write_bytes(str(start_frame).encode())
        return [output]

    monkeypatch.setattr(
        extract_specific_frames,
        "extract_frame_range",
        fake_extract_frame_range,
    )

    extract_specific_frames.extract_selected_frames(
        video_path=tmp_path / "vfr.mp4",
        frame_numbers=[75, 25, 75],
        output_dir=tmp_path / "frames",
        fps=50,
        ext="png",
    )

    assert calls == [(25, 26), (75, 76)]
    assert (tmp_path / "frames" / "frame_0000025.png").read_bytes() == b"25"
    assert (tmp_path / "frames" / "frame_0000075.png").read_bytes() == b"75"


@pytest.mark.unit
def test_selected_frame_extraction_rejects_negative_indices(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        extract_specific_frames.extract_selected_frames(
            video_path=tmp_path / "video.mp4",
            frame_numbers=[-1],
            output_dir=tmp_path / "frames",
        )
