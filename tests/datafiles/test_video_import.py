# from endoreg_db.models import (
#     RawVideoFile,Center,VideoMeta
# )
    
# from datetime import datetime

# from django.core.management import call_command
# from django.test import TestCase
# from io import StringIO
# from .conf import (
#     TEST_VIDEO_DICT,
#     TEST_CENTER_NAME,
#     TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH,
#     check_file_exists,
#     TEST_SALT
# )
# from endoreg_db.utils.hashs import (
#     get_hash_string
# )

# import json

# from pathlib import Path

# with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "w") as f:
#     f.write("Test Gastroscopy Video\n\n")

# class TestVideo(TestCase):
#     def setUp(self):
#         out = StringIO()
#         call_command("load_gender_data", stdout=out)
#         call_command("load_unit_data", stdout=out)
#         call_command("load_name_data", stdout=out)
#         call_command("load_center_data", stdout=out)
#         call_command("load_endoscope_data", stdout=out)
#         call_command("load_green_endoscopy_wuerzburg_data", stdout=out)


#         university_hospital_wuerzburg = Center.objects.get(name=TEST_CENTER_NAME)
#         assert university_hospital_wuerzburg, "Center not found"

#     def test_gastro_video(self):
#         from endoreg_db.models import SensitiveMeta
#         video_dict = TEST_VIDEO_DICT["gastroscopy"]
#         result_dict = video_dict["results"]
        
#         if check_file_exists(video_dict["path"]) and not video_dict["skip"]:
#             center = Center.objects.get(name=TEST_CENTER_NAME)
#             raw_video = RawVideoFile.create_from_file(
#                 file_path=video_dict["path"],
#                 center_name=TEST_CENTER_NAME,
#                 processor_name = video_dict["endoscopy_processor"],
#                 delete_source=False,
#             )

#             imported_video_path = raw_video.file.path
#             self.assertTrue(Path(imported_video_path).exists())
#             self.assertTrue(Path(imported_video_path).is_file())

#             video_meta = raw_video.video_meta
#             with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
#                 f.write(f"Video Meta:\n{video_meta}\n\n")
#                 f.write(f"FFMPEG Meta:\n{video_meta.ffmpeg_meta}\n\n")
#                 endo_roi_dict = video_meta.get_endo_roi()
#                 f.write("Endoscope ROI:\n")
#                 for key, value in endo_roi_dict.items():
#                     f.write(f"\t{key}:\t")
#                     f.write(f"{value}\n")
#                 f.write("\n")

#             extract_frames_result = raw_video.extract_frames(verbose=True)

#             with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
#                 f.write(f"Extract Frames Result:\n{extract_frames_result}\n\n")
                
#             # get frame paths:
#             frame_paths = raw_video.get_frame_paths()
#             for frame_path in frame_paths:
#                 self.assertTrue(Path(frame_path).exists())
#                 self.assertTrue(Path(frame_path).is_file())

#             # Extract Text info and append  to file:
#             raw_video.update_text_metadata(ocr_frame_fraction=0.001)
#             sensitive_meta:SensitiveMeta = raw_video.sensitive_meta
#             with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
#                 f.write(f"Text Info:\n{sensitive_meta}\n\n")

            
#             # check states (substitution, hashing, etc.)
#             # if state not true, then run functions to update states

#             # In Production, the sensitive metadata object must be verified by human annotation
#             sensitive_meta.state_verified = True
#             raw_video.save()

#             patient_hash = raw_video.sensitive_meta.get_patient_hash(salt=TEST_SALT)
#             patient_examination_hash = raw_video.sensitive_meta.get_patient_examination_hash(salt=TEST_SALT)
            
#             patient_hash_string = get_hash_string(
#                 first_name = sensitive_meta.patient_first_name,
#                 last_name = sensitive_meta.patient_last_name,
#                 dob=sensitive_meta.patient_dob,
#                 center_name = sensitive_meta.center.name,
#                 salt=TEST_SALT
#             )

#             patient_examination_hash_string = get_hash_string(
#                 first_name=sensitive_meta.patient_first_name,
#                 last_name=sensitive_meta.patient_last_name,
#                 dob=sensitive_meta.patient_dob,
#                 center_name=sensitive_meta.center.name,
#                 examination_date=sensitive_meta.examination_date,
#                 salt=TEST_SALT
#             )
            
#             with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
#                 f.write(f"Patient String for Hashing:\n\t{patient_hash_string}\n")
#                 f.write(f"Patient Hash:\n\t{patient_hash}\n\n")
#                 f.write(f"Patient Examination String for Hashing:\n\t{patient_examination_hash_string}\n")
#                 f.write(f"Patient Examination Hash:\n\t{patient_examination_hash}\n\n")

#             assert patient_hash==result_dict["patient_hash"], "Patient Hashes do not match"
#             assert patient_examination_hash==result_dict["patient_examination_hash"], "Patient Examination Hashes do not match"

#             frame_dir = raw_video.frame_dir
#             raw_video.delete_with_file()

#             self.assertFalse(Path(imported_video_path).exists())
#             self.assertFalse(Path(frame_dir).exists())

#             with open(TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH, "a") as f:
#                 f.write("Video and Frames Deleted successfully\n\n")

#             return True

#     def test_load_video_legacy(self):
#         out = StringIO()
#         # import würzburg 1920x1080 anonymized videos
#         call_command("load_ai_model_data", stdout=out)



