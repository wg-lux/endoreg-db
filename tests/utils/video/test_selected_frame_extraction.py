# pyright: reportPrivateUsage=false
"""Contract tests for sparse selected-frame extraction by decode index."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import pytest

from endoreg_db.utils.video import frame_extraction
from endoreg_db.utils.video.command_construction import (
    _build_extract_selected_frames_command,
    _selected_frame_runs,
    _selected_frame_select_expression,
)
from endoreg_db.utils.video.executable_discovery import _resolve_ffmpeg_executable

# Each synthetic frame has uniform luminance ``decode index * 10`` so an
# extracted image proves which decoded frame it came from.
_LUMA_STEP = 10
_FRAME_COUNT = 20


@pytest.mark.parametrize(
    ("frame_numbers", "expected_runs"),
    [
        ([0], [(0, 0)]),
        ([4, 5, 6], [(4, 6)]),
        ([1, 3, 4, 9], [(1, 1), (3, 4), (9, 9)]),
        ([0, 2, 4], [(0, 0), (2, 2), (4, 4)]),
    ],
)
def test_selected_frame_runs_group_contiguous_indices(
    frame_numbers: list[int], expected_runs: list[tuple[int, int]]
) -> None:
    assert _selected_frame_runs(frame_numbers) == expected_runs


@pytest.mark.parametrize(
    ("frame_numbers", "message"),
    [
        ([], "At least one"),
        ([-1, 2], "non-negative"),
        ([3, 3], "strictly ascending"),
        ([5, 2], "strictly ascending"),
    ],
)
def test_selected_frame_runs_reject_invalid_input(
    frame_numbers: list[int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _selected_frame_runs(frame_numbers)


def test_select_expression_uses_eq_and_between_terms() -> None:
    assert (
        _selected_frame_select_expression([(1, 1), (3, 5)]) == "eq(n,1)+between(n,3,5)"
    )


def test_selected_frames_command_preserves_decoded_frame_identity() -> None:
    command = _build_extract_selected_frames_command(
        ffmpeg_executable="ffmpeg",
        video_path=Path("/media/in.mp4"),
        output_pattern=Path("/tmp/out/selected_%07d.jpg"),
        runs=[(2, 2), (7, 9)],
        quality=2,
        ext="jpg",
    )

    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert command[command.index("-frames:v") + 1] == "4"
    assert command[command.index("-vf") + 1] == "select='eq(n,2)+between(n,7,9)'"
    assert command[command.index("-map") + 1] == "0:v:0"
    assert command[command.index("-qscale:v") + 1] == "2"


def test_extract_selected_frames_rejects_empty_selection(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one frame"):
        frame_extraction.extract_selected_frames(
            tmp_path / "in.mkv", tmp_path / "out", frame_numbers=[], quality=2
        )


def _write_indexed_video(
    ffmpeg: str,
    output_path: Path,
    *,
    variable_frame_rate: bool,
    display_rotation: int | None = None,
) -> None:
    timeline = (
        # 10 frames at 100 ms, then 10 frames at 40 ms.
        ",settb=1/1000,setpts='if(lt(N\\,10)\\,N*0.1\\,1+(N-10)*0.04)/TB'"
        if variable_frame_rate
        else ""
    )
    source = (
        f"color=c=black:s=32x16:r=10:d={_FRAME_COUNT / 10},format=gray,"
        f"geq=lum='N*{_LUMA_STEP}'{timeline}"
    )
    rotation_args = (
        [] if display_rotation is None else ["-display_rotation", str(display_rotation)]
    )
    encoded = output_path.with_name(f"encoded_{output_path.name}")
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-fps_mode",
            "passthrough",
            "-enc_time_base:v",
            "1/1000",
            "-c:v",
            "ffv1",
            str(encoded),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            *rotation_args,
            "-i",
            str(encoded),
            "-c",
            "copy",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def _decoded_index(image_path: Path) -> int:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    assert image is not None, image_path
    return round(float(image.mean()) / _LUMA_STEP)


@pytest.mark.ffmpeg
@pytest.mark.parametrize("variable_frame_rate", [False, True])
@pytest.mark.parametrize("max_runs_per_pass", [256, 1])
def test_extract_selected_frames_writes_only_requested_decode_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable_frame_rate: bool,
    max_runs_per_pass: int,
) -> None:
    ffmpeg = _resolve_ffmpeg_executable()
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    video_path = tmp_path / "indexed.mkv"
    _write_indexed_video(ffmpeg, video_path, variable_frame_rate=variable_frame_rate)
    monkeypatch.setattr(
        frame_extraction, "_MAX_SELECTED_RUNS_PER_PASS", max_runs_per_pass
    )
    output_dir = tmp_path / "frames"
    requested = [16, 0, 3, 4, 5, 12, 4]

    extracted = frame_extraction.extract_selected_frames(
        video_path, output_dir, frame_numbers=requested, quality=2, ext="png"
    )

    expected = sorted(set(requested))
    assert [path.name for path in extracted] == [
        f"frame_{number:07d}.png" for number in expected
    ]
    assert [_decoded_index(path) for path in extracted] == expected
    # Frames between requested indices are never written, and staging is gone.
    assert sorted(path.name for path in output_dir.iterdir()) == [
        path.name for path in extracted
    ]


@pytest.mark.ffmpeg
def test_extract_selected_frames_applies_display_rotation(tmp_path: Path) -> None:
    ffmpeg = _resolve_ffmpeg_executable()
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    video_path = tmp_path / "rotated.mkv"
    _write_indexed_video(
        ffmpeg, video_path, variable_frame_rate=True, display_rotation=90
    )

    extracted = frame_extraction.extract_selected_frames(
        video_path, tmp_path / "frames", frame_numbers=[2, 11], quality=2, ext="png"
    )

    assert [_decoded_index(path) for path in extracted] == [2, 11]
    image = cv2.imread(str(extracted[0]), cv2.IMREAD_GRAYSCALE)
    assert image is not None
    assert image.shape == (32, 16)


@pytest.mark.ffmpeg
def test_extract_selected_frames_fails_when_index_is_beyond_video(
    tmp_path: Path,
) -> None:
    ffmpeg = _resolve_ffmpeg_executable()
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    video_path = tmp_path / "indexed.mkv"
    _write_indexed_video(ffmpeg, video_path, variable_frame_rate=False)
    output_dir = tmp_path / "frames"

    with pytest.raises(RuntimeError, match="expected=2 actual=1"):
        frame_extraction.extract_selected_frames(
            video_path,
            output_dir,
            frame_numbers=[3, _FRAME_COUNT + 5],
            quality=2,
            ext="png",
        )
    assert not any(output_dir.glob("selected_pass_*"))
