import logging
import os
from contextlib import nullcontext
from pathlib import Path

logger = logging.getLogger(__name__)


def _temp_media_path(final_path: Path, marker: str = "part") -> Path:
    """Keep the media suffix last so FFmpeg can infer the container."""
    return final_path.with_name(f"{final_path.stem}.{marker}{final_path.suffix}")


from lx_anonymizer.frame_cleaner import FrameCleaner
from lx_anonymizer.sensitive_meta_interface import SensitiveMeta as LxSM

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    sensitive_meta_storage,
)
from endoreg_db.models import EndoscopyProcessor, VideoFile
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
)
from endoreg_db.utils.video.ffmpeg_wrapper import (
    _resolve_ffmpeg_executable,
    _resolve_ffprobe_executable,
)


def _processed_video_dir() -> Path:
    return (
        path_utils.EndoregPathsModel.from_environment().transcoding
        / "anonymized_videos"
    )


def _ensure_ffmpeg_tools_on_path() -> None:
    """
    lx_anonymizer shells out to "ffmpeg" and "ffprobe" by executable name.

    endoreg_db supports Nix-style deployments where FFmpeg is discoverable via
    explicit resolver logic but not necessarily present on PATH. Prepending the
    resolved tool directory keeps both integrations using the same binaries.
    """
    ffmpeg_path = _resolve_ffmpeg_executable()
    ffprobe_path = _resolve_ffprobe_executable()
    if not ffmpeg_path or not ffprobe_path:
        raise RuntimeError(
            "FFmpeg and ffprobe are required for video anonymization but could not be resolved."
        )

    ffmpeg_dir = Path(ffmpeg_path).parent
    ffprobe_dir = Path(ffprobe_path).parent
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    prepend_dirs = []
    for path in (ffmpeg_dir, ffprobe_dir):
        path_value = path.as_posix()
        if path_value not in path_parts and path_value not in prepend_dirs:
            prepend_dirs.append(path_value)
    if prepend_dirs:
        os.environ["PATH"] = os.pathsep.join(
            [*prepend_dirs, os.environ.get("PATH", "")]
        )
        logger.info(
            "Prepended FFmpeg tool directories to PATH for lx_anonymizer: %s",
            prepend_dirs,
        )


class VideoAnonymizer:
    def __init__(self):
        _ensure_ffmpeg_tools_on_path()
        self._ensure_frame_cleaning_available()
        self._frame_cleaning_available = None
        self._frame_cleaning_class = None

    def anonymize_video(self, ctx: ImportContext):
        _ensure_ffmpeg_tools_on_path()
        # Setup anonymized directory
        anonymized_dir = ensure_directory(_processed_video_dir())
        assert ctx.current_video is not None
        # Generate output path for anonymized report

        video_hash = ctx.current_video.video_hash
        anonymized_output_path = anonymized_dir / f"{video_hash}.mp4"
        temp_output_path = _temp_media_path(anonymized_output_path)
        safe_unlink_file(temp_output_path, missing_ok=True)

        self._frame_cleaning_class = FrameCleaner()

        assert isinstance(self._frame_cleaning_class, FrameCleaner)
        endoscope_roi, endoscope_roi_nested = self._get_processor_roi_info(ctx)
        ensure_raw = getattr(ctx.current_video, "ensure_local_raw_file", None)
        if callable(ensure_raw):
            source_context = ensure_raw()
        else:
            source_path = ctx.sensitive_path or ctx.file_path
            source_context = nullcontext(source_path)

        # Process with enhanced process_report method (returns 4-tuple now)
        with source_context as source_path:
            if source_path is None:
                raise RuntimeError(
                    f"Video anonymization source is unavailable for {video_hash}."
                )
            ctx.anonymized_path, extracted_metadata = (
                self._frame_cleaning_class.clean_video(
                    video_path=Path(source_path),
                    endoscope_image_roi=endoscope_roi,
                    endoscope_data_roi_nested=endoscope_roi_nested,
                    output_path=temp_output_path,
                )
            )
        if ctx.anonymized_path is None:
            raise RuntimeError("Video anonymization returned no output path.")

        temp_result_path = ctx.anonymized_path
        if not temp_result_path.exists():
            raise RuntimeError(
                f"Video anonymization output does not exist: {temp_result_path}"
            )
        if temp_result_path.stat().st_size <= 0:
            raise RuntimeError(
                f"Video anonymization output is empty: {temp_result_path}"
            )

        atomic_move_file(source=temp_result_path, destination=anonymized_output_path)
        ctx.anonymized_path = anonymized_output_path
        sm = LxSM()
        sm.safe_update(extracted_metadata)

        sensitive_meta_storage(sm, ctx.current_video)
        return ctx

    def _ensure_frame_cleaning_available(self):
        """
        Ensure frame cleaning modules are available by adding lx-anonymizer to path.

        Returns:
            Tuple of (availability_flag, FrameCleaner_class, ReportReader_class)
        """
        try:
            from lx_anonymizer import FrameCleaner
        except Exception as e:
            logger.warning(
                f"Frame cleaning not available: {e} Please install or update lx_anonymizer."
            )
            raise

        assert FrameCleaner is not None
        self._frame_cleaning_class = FrameCleaner()
        self._frame_cleaning_available = True

    def _get_processor_roi_info(
        self,
        ctx: ImportContext,
    ) -> tuple[
        dict[str, int | None] | None, dict[str, dict[str, int | None] | None] | None
    ]:
        """Get processor ROI information for masking and data extraction."""
        endoscope_data_roi_nested = None
        endoscope_image_roi = None

        video = ctx.current_video
        assert isinstance(video, VideoFile)

        try:
            processor_name = ctx.processor_name if ctx.processor_name else None
            if processor_name:
                pr = EndoscopyProcessor()
                processor = pr.get_by_name(processor_name)
                assert isinstance(processor, EndoscopyProcessor), (
                    "Processor is not of type EndoscopyProcessor"
                )
                endoscope_image_roi = processor.get_roi_endoscope_image()
                endoscope_data_roi_nested = processor.get_sensitive_rois()
                logger.info(
                    "Retrieved processor ROI information: endoscope_image_roi=%s",
                    endoscope_image_roi,
                )
            else:
                logger.warning(
                    "No processor found for video %s, proceeding without ROI masking",
                    video.video_hash,
                )
        except Exception as exc:
            logger.error("Failed to retrieve processor ROI information: %s", exc)

        # IMPORTANT: return order must match clean_video signature
        return endoscope_image_roi, endoscope_data_roi_nested
