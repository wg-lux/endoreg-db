from __future__ import annotations

from dataclasses import dataclass

from endoreg_db.config.env import DEFAULT_VIDEO_FPS


@dataclass(frozen=True, slots=True)
class VideoEncodingStandard:
    """Canonical video-stream properties shared by MP4 and HLS outputs."""

    codec_name: str = "h264"
    encoder: str = "libx264"
    profile: str = "high"
    pixel_format: str = "yuv420p"
    color_range: str = "pc"
    max_fps: float = DEFAULT_VIDEO_FPS

    def max_fps_arg(self) -> str:
        return (
            str(int(self.max_fps)) if self.max_fps.is_integer() else f"{self.max_fps:g}"
        )

    def filter_chain(self, *, height_px: int | None = None) -> str:
        size = "iw:ih" if height_px is None else f"-2:{height_px}"
        return f"scale={size}:in_range=auto:out_range=full,format={self.pixel_format}"

    def timestamp_passthrough_args(self) -> list[str]:
        """Keep decoded presentation timestamps, their origin and time-base precision."""
        return ["-copyts", "-fps_mode", "passthrough", "-enc_time_base", "demux"]

    def ffmpeg_output_args(
        self,
        *,
        height_px: int | None = None,
        max_fps: float | None = None,
    ) -> list[str]:
        timing_args = (
            self.timestamp_passthrough_args()
            if max_fps is None
            else ["-fpsmax", f"{max_fps:g}"]
        )
        return [
            "-profile:v",
            self.profile,
            "-vf",
            self.filter_chain(height_px=height_px),
            "-pix_fmt",
            self.pixel_format,
            "-color_range",
            self.color_range,
            *timing_args,
        ]


STANDARD_VIDEO_ENCODING = VideoEncodingStandard()
