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

    def ffmpeg_output_args(
        self,
        *,
        height_px: int | None = None,
        max_fps: float | None = None,
    ) -> list[str]:
        target_max_fps = self.max_fps if max_fps is None else max_fps
        max_fps_arg = (
            str(int(target_max_fps))
            if float(target_max_fps).is_integer()
            else f"{target_max_fps:g}"
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
            "-fpsmax",
            max_fps_arg,
        ]


STANDARD_VIDEO_ENCODING = VideoEncodingStandard()
