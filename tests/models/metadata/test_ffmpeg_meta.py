from __future__ import annotations

from typing import cast
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from lx_dtypes.models.contracts.ffmpeg_metadata import FfmpegProbeDataPayload

from endoreg_db.models.metadata.video_meta import FFMpegMeta


def _valid_probe_data() -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "width": 640,
                "height": 480,
                "duration": "12.5",
                "r_frame_rate": "25/1",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "bit_rate": "1000",
            }
        ],
        "format": {"duration": "12.5", "bit_rate": "1200"},
    }


@pytest.mark.django_db
def test_ffmpeg_meta_canonicalizes_raw_probe_data_on_round_trip() -> None:
    meta = FFMpegMeta.objects.create(
        width=640,
        height=480,
        raw_probe_data=_valid_probe_data(),
    )

    meta.refresh_from_db()

    expected = FfmpegProbeDataPayload.model_validate(_valid_probe_data()).model_dump(
        mode="json"
    )
    assert meta.raw_probe_data == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_probe_data",
    [
        {"streams": [{"codec_type": "video", "width": "640"}]},
        {"streams": [{"codec_type": "video", "unexpected": "value"}]},
        {"streams": "not-a-list"},
    ],
)
def test_ffmpeg_meta_rejects_invalid_raw_probe_data(
    invalid_probe_data: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        FFMpegMeta.objects.create(raw_probe_data=invalid_probe_data)

    assert "raw_probe_data" in exc_info.value.message_dict


@pytest.mark.django_db
def test_create_from_file_accepts_real_ffprobe_extra_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"not a real video; ffprobe is mocked")

    def fake_get_stream_info(path: Path) -> dict[str, object]:
        assert path == video_path
        return {
            "streams": [
                {
                    "index": 0,
                    "codec_name": "h264",
                    "codec_long_name": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10",
                    "profile": "High",
                    "codec_type": "video",
                    "codec_tag_string": "avc1",
                    "width": 480,
                    "height": 270,
                    "coded_width": 480,
                    "coded_height": 270,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "25/1",
                    "avg_frame_rate": "25/1",
                    "duration": "30.040000",
                    "bit_rate": "246155",
                    "disposition": {"default": 1, "dub": 0},
                    "tags": {"language": "und"},
                },
                {
                    "index": 1,
                    "codec_name": "aac",
                    "codec_long_name": "AAC (Advanced Audio Coding)",
                    "codec_type": "audio",
                    "sample_fmt": "fltp",
                    "sample_rate": "48000",
                    "channels": 2,
                    "channel_layout": "stereo",
                    "duration": "30.528000",
                    "bit_rate": "128000",
                },
            ],
            "format": {
                "duration": "30.040000",
                "bit_rate": "369334",
                "format_long_name": "QuickTime / MOV",
            },
        }

    monkeypatch.setattr(
        "endoreg_db.models.metadata.video_meta.ffmpeg_wrapper.get_stream_info",
        fake_get_stream_info,
    )

    ffmpeg_meta = FFMpegMeta.create_from_file(video_path)

    assert ffmpeg_meta.width == 480
    assert ffmpeg_meta.height == 270
    assert ffmpeg_meta.duration == 30.04
    assert ffmpeg_meta.frame_rate_num == 25
    assert ffmpeg_meta.frame_rate_den == 1
    assert ffmpeg_meta.codec_name == "h264"
    assert ffmpeg_meta.pixel_format == "yuv420p"
    assert ffmpeg_meta.bit_rate == 246155
    raw_probe_data = ffmpeg_meta.raw_probe_data
    assert raw_probe_data is not None
    streams = raw_probe_data["streams"]
    assert isinstance(streams, list)
    first_stream = cast(dict[str, object], streams[0])
    assert isinstance(first_stream, dict)
    assert "codec_long_name" not in first_stream
