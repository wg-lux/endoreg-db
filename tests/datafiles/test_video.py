from endoreg_db.models import (
    RawVideoFile,Center,VideoMeta
)
    
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_VIDEO_DICT,
    TEST_CENTER_NAME,
    TEST_FRAME_DIR,
    TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH,
    check_file_exists
)

from pathlib import Path


class TestVideo(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_name_data", stdout=out)
        call_command("load_center_data", stdout=out)
        call_command("load_endoscope_data", stdout=out)
        call_command("load_green_endoscopy_wuerzburg_data", stdout=out)

        with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "w") as f:
            f.write("Test Gastroscopy Video\n")


    def test_gastro_video(self):
        video_dict = TEST_VIDEO_DICT["gastroscopy"]
        
        if check_file_exists(video_dict["path"]) and not video_dict["skip"]:
            center = Center.objects.get(name=TEST_CENTER_NAME)
            raw_video = RawVideoFile.create_from_file(
                file_path=video_dict["path"],
                center_name=TEST_CENTER_NAME,
                processor_name = video_dict["endoscopy_processor"],
                delete_source=False,
            )

            imported_video_path = raw_video.file.path
            self.assertTrue(Path(imported_video_path).exists())
            self.assertTrue(Path(imported_video_path).is_file())

            video_meta = raw_video.video_meta
            with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
                f.write(f"Video Meta:\n{video_meta}\n\n")
                f.write(f"FFMPEG Meta:\n{video_meta.ffmpeg_meta}\n\n")
                endo_roi_dict = video_meta.get_endo_roi()
                f.write("Endoscope ROI:\n")
                for key, value in endo_roi_dict.items():
                    f.write(f"\t{key}:\t")
                    f.write(f"{value}\n")
                f.write("\n")

            extract_frames_result = raw_video.extract_frames(verbose=True)

            with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
                f.write(f"Extract Frames Result:\n{extract_frames_result}\n\n")
                
            # get frame paths:
            frame_paths = raw_video.get_frame_paths()
            for frame_path in frame_paths:
                self.assertTrue(Path(frame_path).exists())
                self.assertTrue(Path(frame_path).is_file())


            raw_video.delete_with_file()

            self.assertFalse(Path(imported_video_path).exists())




