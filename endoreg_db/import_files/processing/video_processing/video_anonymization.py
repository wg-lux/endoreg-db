import logging

logger = logging.getLogger(__name__)

from lx_anonymizer import FrameCleaner
from lx_anonymizer.sensitive_meta_interface import SensitiveMeta as LxSM

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    sensitive_meta_storage,
)
from endoreg_db.models import EndoscopyProcessor, VideoFile
from endoreg_db.utils.paths import ANONYM_VIDEO_DIR


class VideoAnonymizer:
    def __init__(self):
        self._ensure_frame_cleaning_available()
        self._frame_cleaning_available = None
        self._frame_cleaning_class = None
        self.storage = False

    def anonymize_video(self, ctx: ImportContext):
        # Setup anonymized directory
        anonymized_dir = ANONYM_VIDEO_DIR
        anonymized_dir.mkdir(parents=True, exist_ok=True)
        assert ctx.current_video is not None
        # Generate output path for anonymized report

        video_hash = ctx.current_video.video_hash
        anonymized_output_path = anonymized_dir / f"{video_hash}.mp4"

        self._frame_cleaning_class = FrameCleaner()

        assert isinstance(self._frame_cleaning_class, FrameCleaner)
        endoscope_roi, endoscope_roi_nested = self._get_processor_roi_info(ctx)
        # Process with enhanced process_report method (returns 4-tuple now)
        ctx.anonymized_path, extracted_metadata = (
            self._frame_cleaning_class.clean_video(
                video_path=ctx.file_path,
                endoscope_image_roi=endoscope_roi,
                endoscope_data_roi_nested=endoscope_roi_nested,
                output_path=anonymized_output_path,
            )
        )
        sm = LxSM()
        sm.safe_update(extracted_metadata)

        self.storage = sensitive_meta_storage(sm, ctx.current_video)
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
