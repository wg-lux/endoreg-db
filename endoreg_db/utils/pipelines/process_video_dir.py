from endoreg_db.models import (
    VideoFile, Center, EndoscopyProcessor, AiModel, ModelMeta,
    LabelVideoSegment, SensitiveMeta
)
from pathlib import Path
from icecream import ic
from tqdm import tqdm
import os
from django.core.management import call_command
import logging
from django.core.exceptions import ObjectDoesNotExist

logger = logging.getLogger(__name__)
# Usage
# from endoreg_db.utils.pipelines.process_video_dir import process_video_dir
# (video_dir=Path("/path/to/your/videos"))

# DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data/raw_videos"
DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data/study_data"
CENTER_NAME = os.environ.get("CENTER_NAME", "university_hospital_wuerzburg")
ENDOSCOPY_PROCESSOR_NAME = os.environ.get("ENDOSCOPY_PROCESSOR_NAME", "olympus_cv_1500")
MODEL_NAME = "image_multilabel_classification_colonoscopy_default"
MODEL_WEIGHTS_PATH = "./tests/assets/colo_segmentation_RegNetX800MF_6.safetensors"
ic(DEFAULT_DIR)



def process_video_dir(video_dir: Path=DEFAULT_DIR, center_name: str = CENTER_NAME, endoscopy_processor_name: str = ENDOSCOPY_PROCESSOR_NAME):
    """
    Process a directory of video files and create VideoFile objects in the database.
    
    Args:
        video_dir (Path): Path to the directory containing video files.
        center_name (str): Name of the center to associate with the video files.
        endoscopy_processor_name (str): Name of the endoscopy processor to associate with the video files.
    """
    # Make sure the ai model exists:
    ai_model = AiModel.objects.filter(name=MODEL_NAME)
    if ai_model:
        ai_model = ai_model.first()
    try:
        model_meta = ai_model.get_latest_version()
        assert isinstance(model_meta, ModelMeta), "No ModelMeta found in the database."
    except Exception:
        logger.warning(f"ModelMeta not found for {MODEL_NAME}. Creating a new one.")
        # Create a new ModelMeta instance
        call_command(
            "create_multilabel_model_meta",
            "--model_path",
            MODEL_WEIGHTS_PATH,
        )



    # Get the center and endoscopy processor objects

    # Iterate through all files in the directory
    for file_path in tqdm(video_dir.iterdir()):
        logger.info(f"Processing file: {file_path}")
        if file_path.is_file() and file_path.suffix in ['.mp4', '.avi', '.mov']:
            # Create a VideoFile object
            video_file = VideoFile.create_from_file_initialized(
                file_path=file_path,
                center_name=center_name,
                processor_name=endoscopy_processor_name,
            )
            logger.warning(f"Processing video file: {video_file}")
            try:
                success_pipe_1 = video_file.pipe_1(
                    model_name = MODEL_NAME,
                )
                assert success_pipe_1, f"Pipe 1 failed for video {video_file.uuid}"
            except Exception as e:
                logger.error(f"Pipe 1 failed for video {video_file.uuid}: {e}", exc_info=True)
                raise e

            video_file.refresh_from_db()

            ####### SIMULATION OF VALIDATION #######
            # Simulate the validation process for video segments
            outside_segments = video_file.get_outside_segments()
            logger.warning(f"Outside segments found for simulation: {outside_segments}")
            if outside_segments:
                for segment in outside_segments:
                    try:
                        seg_state = segment.state
                        seg_state.is_validated = True
                        seg_state.save()
                        logger.info(f"Simulated validation for segment {segment.id}")
                    except LabelVideoSegment.state.RelatedObjectDoesNotExist:
                        logger.warning(f"Cannot simulate validation for segment {segment.id}: Related state object does not exist.")
                    except Exception as e:
                        logger.error(f"Error during segment state simulation for segment {segment.id}: {e}", exc_info=True)


            # Simulate the validation process for the video files sensitive meta obj
            if video_file.sensitive_meta:
                try:
                    sm_state = video_file.sensitive_meta.state
                    sm_state.dob_verified = True
                    sm_state.names_verified = True
                    sm_state.save()
                    logger.info(f"Simulated validation for sensitive meta of video {video_file.uuid}")
                except Exception as e:
                    logger.error(f"Error during sensitive meta state simulation for video {video_file.uuid}: {e}", exc_info=True)

            else:
                logger.warning(f"Cannot simulate validation for sensitive meta of video {video_file.uuid}: SensitiveMeta object does not exist (likely due to pipe_1 failure or incomplete text extraction).")

            ######## SIMULATION OF VALIDATION ########

            video_file.refresh_from_db()
            if success_pipe_1:
                try:
                    video_file.pipe_2()
                except Exception as e:
                    logger.error(f"Pipe 2 failed for video {video_file.uuid}: {e}", exc_info=True)
            else:
                logger.warning(f"Skipping Pipe 2 for video {video_file.uuid} because Pipe 1 did not complete successfully.")

