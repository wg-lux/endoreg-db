from ..raw_video import RawVideoFile
import json 

# # Starting point
# Automated tasks generate RawVideoFile objects in our db.
# Each object has state_{NAME} attributes.
# We will create functions which query the db for RawVideoFile 
# objects with specific state_{NAME} attributes.
# Then, we perform the necessary operations on the RawVideoFile and
# update the state_{NAME} attributes accordingly.

# # Step 1 - Frame Extraction
# function to query for videos scheduled for frame extraction,
# these have state_frames_required and state_frames_extracted
def get_videos_scheduled_for_frame_extraction():
    return RawVideoFile.objects.filter(
        state_frames_required=True,
        state_frames_extracted=False
    )

def extract_frames_from_video(video:RawVideoFile):
    # extract frames from video
    video.extract_frames()

    # update state_frames_extracted
    video.state_frames_extracted = True
    video.save()

    return video

def extract_frames_from_videos():
    videos = get_videos_scheduled_for_frame_extraction()
    for video in videos:
        extract_frames_from_video(video)

# # Step 2 - OCR
# function to query for videos scheduled for OCR,
# these have 
# state_ocr_required = True and state_ocr_completed = False and state_frames_extracted = True
def get_videos_scheduled_for_ocr():
    return RawVideoFile.objects.filter(
        state_ocr_required=True,
        state_ocr_completed=False,
        state_frames_extracted=True
    )

# function to set state_frames_required to True for videos 
# which are scheduled for OCR but have not had frames extracted
def videos_scheduled_for_ocr_preflight():
    videos = RawVideoFile.objects.filter(
        state_ocr_required=True,
        state_ocr_completed=False,
        state_frames_extracted=False
    )
    for video in videos:
        video.state_frames_required = True
        video.save()

def perform_ocr_on_video(video:RawVideoFile):
    # perform OCR on video
    video.update_text_metadata()

    # update state_ocr_completed
    video.state_ocr_completed = True
    video.save()

    return video

def perform_ocr_on_videos():
    videos = get_videos_scheduled_for_ocr()
    for video in videos:
        perform_ocr_on_video(video)


# # Step 3 - initial Prediction
# function to query for videos scheduled for initial prediction,
# these have
# state_initial_prediction_required = True and state_initial_prediction_completed = False and state_frames_extracted = True
def videos_scheduled_for_initial_prediction_preflight():
    videos = RawVideoFile.objects.filter(
        state_initial_prediction_required=True,
        state_initial_prediction_completed=False,
        state_frames_extracted=False
    )
    for video in videos:
        video.state_frames_required = True
        video.save()

def get_videos_scheduled_for_initial_prediction():
    return RawVideoFile.objects.filter(
        state_initial_prediction_required=True,
        state_initial_prediction_completed=False,
        state_frames_extracted=True
    )

from pathlib import Path
def get_multilabel_model(model_path:Path):
    from agl_predict_endo_frame.model_loader import MultiLabelClassificationNet
    model_path_str = model_path.resolve().as_posix()
    model = MultiLabelClassificationNet.load_from_checkpoint(model_path_str)
    model.cuda()
    model.eval()
    return model

def get_multilabel_classifier(model, verbose:bool=False):
    from agl_predict_endo_frame.predict import Classifier
    classifier = Classifier(model, verbose = verbose)
    return classifier

def get_crops(video, paths):
    endo_roi_dict = video.get_endo_roi()
    # dict with x, y, width height
    # crops is list of touples with (y_min, y_max, x_min, x_max)
    crop_tuple = (
        endo_roi_dict["y"],
        endo_roi_dict["y"] + endo_roi_dict["height"],
        endo_roi_dict["x"],
        endo_roi_dict["x"] + endo_roi_dict["width"],  
    )
    crops = [crop_tuple for _ in paths]
    return crops

# model = MultiLabelClassificationNet.load_from_checkpoint("model/colo_segmentation_RegNetX800MF_6.ckpt")
def perform_initial_prediction_on_video(
    video:RawVideoFile, model_path,
    window_size_s, min_seq_len_s
):

    model = get_multilabel_model(model_path)
    classifier = get_multilabel_classifier(model, verbose = True)

    paths = video.get_frame_paths()
    string_paths = [p.resolve().as_posix() for p in paths]
    crops = get_crops(video, string_paths)
    fps = video.get_fps()

    predictions = classifier.pipe(string_paths, crops)
    readable_predictions = [classifier.readable(p) for p in predictions]
    result_dict = classifier.post_process_predictions_serializable(
        readable_predictions,
        window_size_s = window_size_s,
        min_seq_len_s = min_seq_len_s,
        fps = fps
    )

    
    # Predictions
    _path = video.get_predictions_path()
    with open(_path, "w") as f:
        json.dump(result_dict["predictions"], f, indent = 4)

    # smooth_predictions
    _path = video.get_smooth_predictions_path()
    with open(_path, "w") as f:
        json.dump(result_dict["smooth_predictions"], f, indent = 4)

    # binary_predictions
    _path = video.get_binary_predictions_path()
    with open(_path, "w") as f:
        json.dump(result_dict["binary_predictions"], f, indent = 4)

    # Raw Sequences
    _path = video.get_raw_sequences_path()
    with open(_path, "w") as f:
        json.dump(result_dict["raw_sequences"], f, indent = 4)

    # filtered_sequences
    _path = video.get_filtered_sequences_path()
    with open(_path, "w") as f:
        json.dump(result_dict["filtered_sequences"], f, indent = 4)

    
    # update state_initial_prediction_completed
    video.state_initial_prediction_required = False
    video.state_initial_prediction_completed = True
    video.state_initial_prediction_import_required = True
    video.state_initial_prediction_import_completed = False
    video.save()

    return video

def perform_initial_prediction_on_videos(
    model_path,
    window_size_s, min_seq_len_s
):
    videos = get_videos_scheduled_for_initial_prediction()
    for video in videos:
        perform_initial_prediction_on_video(
        video,
        model_path, window_size_s, min_seq_len_s
    )

def videos_scheduled_for_prediction_import_preflight():
    videos = RawVideoFile.objects.filter(
        state_initial_prediction_completed=True,
        state_initial_prediction_import_completed=False
    )
    for video in videos:
        video.state_initial_prediction_required = True
        video.save()
    
def get_videos_scheduled_for_prediction_import():
    return RawVideoFile.objects.filter(
        state_initial_prediction_import_required=True,
        state_initial_prediction_import_completed=False,
        state_initial_prediction_completed=True
    )

def import_predictions_for_video(video:RawVideoFile):
    # import predictions for video
    pass

    # update state_prediction_import_completed
    # video.state_initial_prediction_import_required = False
    # video.state_initial_prediction_import_completed = True
    # video.save()

    return video

def import_predictions_for_videos():
    videos = get_videos_scheduled_for_prediction_import()
    for video in videos:
        import_predictions_for_video(video)

# # Step 4 - Delete Frames if not needed anymore
# function to query for videos scheduled for frame deletion,
# first we need to set state_frames_required = False for videos with:
# state_ocr_required = False and state_ocr_completed = True and
# state_initial_prediction_required = False and state_initial_prediction_completed = True
def delete_frames_preflight():
    videos = RawVideoFile.objects.filter(
        state_ocr_required=False,
        state_ocr_completed=True,
        state_initial_prediction_required=False,
        state_initial_prediction_completed=True
    )
    for video in videos:
        video.state_frames_required = False
        video.save()

# function to query for videos scheduled for frame deletion,
# frames should be deleted if state_frames_required = False
def get_videos_scheduled_for_frame_deletion():
    return RawVideoFile.objects.filter(
        state_frames_required=False
    )

def delete_frames_for_video(video:RawVideoFile):
    # delete frames for video

    # update state_frames_deleted
    video.state_frames_extracted = False
    video.save()

    return video

def delete_frames():
    videos = get_videos_scheduled_for_frame_deletion()
    for video in videos:
        delete_frames_for_video(video)
