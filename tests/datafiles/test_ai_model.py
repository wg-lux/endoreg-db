# from endoreg_db.models import (
#     RawVideoFile,Center,VideoMeta
# )
    
# from datetime import datetime

# from django.core.management import call_command
# from django.test import TestCase
# from io import StringIO
# from .conf import (
#     TEST_MULTILABEL_AI_MODEL_IMPORT_PATH,
#     TEST_CENTER_NAME,
#     TEST_VIDEO_DICT,
#     TEST_MULTILABEL_CLASSIFIER_MODEL_PATH,
# )
# from endoreg_db.utils.hashs import (
#     get_hash_string,
# )

# from torch import nn
# import ssl

# ssl._create_default_https_context = ssl._create_unverified_context

# from pathlib import Path

# with open(TEST_MULTILABEL_AI_MODEL_IMPORT_PATH, "w") as f:
#     f.write("Test Multilabel AI Model\n\n")


# AI_SAMPLE_CONFIG = {
#     # mean and std for normalization
#     "mean": (0.45211223, 0.27139644, 0.19264949),
#     "std": (0.31418097, 0.21088019, 0.16059452),
#     # Image Size
#     "size_x": 716,
#     "size_y": 716,
#     # how to wrangle axes of the image before putting them in the network
#     "axes": [2,0,1],  # 2,1,0 for opencv
#     "batchsize": 16,
#     "num_workers": 8, # always 1 for Windows systems # FIXME: fix celery crash if multiprocessing
#     # maybe add sigmoid after prediction?
#     "activation": nn.Sigmoid(),
#     "labels": [
#         'appendix',  'blood',  'diverticule',  'grasper',  'ileocaecalvalve',  'ileum',  'low_quality',  'nbi',  'needle',  'outside',  'polyp',  'snare',  'water_jet',  'wound'
#     ]
# }

# EXTRACTED_TEST_FRAME_DIR = Path("tests/sensitive/frames")

# class TestMultilabelPredictionModel(TestCase):
#     def setUp(self):
#         out = StringIO()
#         call_command("load_gender_data", stdout=out)
#         call_command("load_unit_data", stdout=out)
#         call_command("load_name_data", stdout=out)
#         call_command("load_center_data", stdout=out)
#         call_command("load_endoscope_data", stdout=out)
#         call_command("load_ai_model_data", stdout=out)

#         university_hospital_wuerzburg = Center.objects.get(name=TEST_CENTER_NAME)
#         assert university_hospital_wuerzburg, "Center not found"

#     def test_load_model(self):
#         from endoreg_db.models import RawVideoFile
#         from endoreg_db.models.ai_model.lightning import (
#             MultiLabelClassificationNet,
#             Classifier,
#             InferenceDataset
#         )
        
#         test_video_dict = TEST_VIDEO_DICT["gastroscopy"]
#         center = Center.objects.get(name=TEST_CENTER_NAME)
        
#         model = MultiLabelClassificationNet.load_from_checkpoint(
#             checkpoint_path=TEST_MULTILABEL_CLASSIFIER_MODEL_PATH,
#         )

#         classifier = Classifier(model=model, verbose = True)

#         raw_video = RawVideoFile.create_from_file(
#             file_path=test_video_dict["path"],
#             center_name=center.name,
#             processor_name = test_video_dict["endoscopy_processor"],
#             delete_source=False,
#         )

#         # # generate frames for prediction:
#         extract_frames_result = raw_video.extract_frames(verbose=True)
#         raw_video.update_text_metadata(ocr_frame_fraction=0.001)
#         _r = raw_video.generate_anonymized_frames()
#         anonymized_frame_paths = raw_video.get_frame_paths(anonymized=True)
#         paths_string = [_.as_posix() for _ in anonymized_frame_paths]

#         endo_roi = raw_video.get_endo_roi() # {"x": 0, "y": 0, "width": 1920, "height": 1080}
        
#         crops = [
#             [
#                 endo_roi["y"], 
#                 endo_roi["y"] + endo_roi["height"],
#                 endo_roi["x"],
#                 endo_roi["x"] + endo_roi["width"]
#             ] for _ in paths_string
#         ]

#         ds = InferenceDataset(
#             paths=paths_string,
#             crops=crops,
#             config = AI_SAMPLE_CONFIG
#         )

#         fps = raw_video.get_fps()
#         predictions = classifier.pipe(paths_string, crops)

#         with open(TEST_MULTILABEL_AI_MODEL_IMPORT_PATH, "a") as f:
#             f.write(f"Path example (total: {len(paths_string)}): {paths_string[0]}")

#         readable_predictions = [classifier.readable(p) for p in predictions]

#         result = classifier.post_process_predictions(
#             pred_dicts = readable_predictions,
#             window_size_s = 1,
#             min_seq_len_s = 1.5,
#             fps = fps
#         )

#         for i, _dict in enumerate(result):
#             _keys  = list(_dict.keys())
#             for key in _keys:
#                 # if numpy array
#                 if hasattr(_dict[key], "tolist"):
#                     _dict[key] = _dict[key].tolist()
                
#                 # check if list of tuples
#                 # if so, make sure each tuple has 2 elements and split to two lists (start, stop)
#                 if isinstance(_dict[key], tuple):
#                     if all(len(x) == 2 for x in _dict[key]):
#                         _dict[key] = {
#                             f"{key}_start": [x[0] for x in _dict[key]],
#                             f"{key}_stop": [x[1] for x in _dict[key]]
#                         }
#                         del result[i][key]

#         predictions = result[0]
#         smooth_predictions = result[1]
#         binary_predictions = result[2]
#         raw_sequences = result[3]
#         filtered_sequences = result[4]
#         result_names = [
#             "predictions",
#             "predictions_smooth",
#             "predictions_binary",
#             "raw_sequences",
#             "filtered_sequences"
#         ]
#         # save each file as json
#         import json
#         for i, _dict in enumerate(result):
#             with open(f"results_{result_names[i]}.json", "w") as f:
#                 json.dump(_dict, f, indent=4)



#         with open(TEST_MULTILABEL_AI_MODEL_IMPORT_PATH, "a") as f:
#             f.write(f"Extract Frames Result:\n{extract_frames_result}\n\n")
#             f.write(f"Anonymized Frames Result:\n{_r}\n\n")
#             f.write(f"Predictions:\n{result}\n\n")

       
#         # raw_video.delete_with_file()

#         # for frame_path in anonymized_frame_paths:
#         #     assert frame_path.exists(), f"Anonymized Frame {frame_path} does not exist"



#         # with open(TEST_MULTILABEL_AI_MODEL_IMPORT_PATH, "a") as f:
#         #     f.write(f"Extract Frames Result:\n{extract_frames_result}\n\n")
#         #     f.write(f"Anonymized Frames Result:\n{_r}\n\n")

