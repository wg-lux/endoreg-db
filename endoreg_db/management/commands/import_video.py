# See Pipe 1 video file function. 
# 

# 1st Examples from the codebase
#     def test_pipeline(self):
        # """
        # Test the pipeline of the video file.
        # This includes:
        # - Pre-validation processing (pipe_1)
        # - Simulating human validation processing (test_after_pipe_1)
        # - Post-validation processing (pipe_2)
        # """
        # _test_pipe_1(self)
        # mock_video_anonym_annotation(self)
        # _test_pipe_2(self), 

# endoreg-db/tests/media/video/test_pipe_1.py
# tests/media/video/test_pipe_2.py
# tests/media/video/test_video_file_extracted.py


# 2nd Example

# def get_default_video_file():
#     from ..media.video.helper import get_random_video_path_by_examination_alias
#     from endoreg_db.models import VideoFile
#     from .data_loader import (
#         load_disease_data,
#         load_event_data,
#         load_information_source,
#         load_examination_data,
#         load_center_data,
#         load_endoscope_data,
#         load_ai_model_label_data,
#         load_ai_model_data,
#     )
#     load_disease_data()
#     load_event_data()
#     load_information_source()
#     load_examination_data()
#     load_center_data()
#     load_endoscope_data()
#     load_ai_model_label_data()
#     load_ai_model_data()
#     video_path = get_random_video_path_by_examination_alias(
#         examination_alias='egd', is_anonymous=False
#     )

#     video_file = VideoFile.create_from_file(
#         file_path=video_path,
#         center_name=DEFAULT_CENTER_NAME,  # Pass center name as expected by _create_from_file
#         delete_source=False,  # Keep the original asset for other tests
#         processor_name = DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
#     )

#     return video_file
