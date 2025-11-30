from typing import List, Dict, Any, Tuple, Optional

import logging
logger = logging.getLogger(__name__)

from lx_anonymizer import FrameCleaner


from endoreg_db.import_files.storage.sensitive_meta_storage import sensitive_meta_storage
from endoreg_db.import_files.context import ImportContext
from endoreg_db.utils.paths import ANONYM_VIDEO_DIR
from endoreg_db.models import EndoscopyProcessor, SensitiveMeta, VideoFile

class VideoAnonymizer:
    def __init__(self):
        self._ensure_frame_cleaning_available()
        self._frame_cleaning_available = None
        self._frame_cleaning_class = None
        self.storage = False

        
    def anonymize_video(self, ctx: ImportContext):
        # Setup anonymized directory
        self.ctx = ctx
        anonymized_dir = ANONYM_VIDEO_DIR
        anonymized_dir.mkdir(parents=True, exist_ok=True)
        assert self.ctx.current_video is not None
        # Generate output path for anonymized report
        
        video_hash = self.ctx.current_video.video_hash
        anonymized_output_path = anonymized_dir / f"{video_hash}.pdf"
        
        assert isinstance(self._frame_cleaning_class, FrameCleaner)
        endoscope_roi, endoscope_roi_nested = self._get_processor_roi_info()
        # Process with enhanced process_report method (returns 4-tuple now)
        self.ctx.anonymized_path, extracted_metadata = self._frame_cleaning_class.clean_video(
                video_path=self.ctx.file_path,
                endoscope_image_roi=endoscope_roi, # type: ignore
                endoscope_data_roi_nested=endoscope_roi_nested,
                output_path=anonymized_output_path
                
            )
    
        self.storage = sensitive_meta_storage(extracted_metadata, self.ctx.current_video)
        if self.storage:
            return self.ctx
        else:
            raise Exception
                    
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
    ) -> Tuple[Optional[List[List[Dict[str, Any]]]], Optional[Dict[str, Any]]]:
        """Get processor ROI information for masking."""
        endoscope_data_roi_nested = None
        endoscope_image_roi = None

        video = self.ctx.current_video

        try:
            video_meta = getattr(video, "video_meta", None)
            processor = self.ctx.processor_name if self.ctx.processor_name else None
            if processor:
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
                    video.uuid,
                )
        except Exception as exc:
            logger.error("Failed to retrieve processor ROI information: %s", exc)

        # Convert dict to nested list if necessary to match return type
        if isinstance(endoscope_data_roi_nested, dict):
            # Convert dict[str, dict[str, int | None] | None] to List[List[Dict[str, Any]]]
            converted_roi = []
            for key, value in endoscope_data_roi_nested.items():
                if isinstance(value, dict):
                    converted_roi.append([value])
                elif value is None:
                    converted_roi.append([])
            endoscope_data_roi_nested = converted_roi

        return endoscope_data_roi_nested, endoscope_image_roi