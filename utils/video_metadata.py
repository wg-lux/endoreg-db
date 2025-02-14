# import cv2
from pathlib import Path
import datetime

def _extract_metadata_from_filename(filepath:Path, time_format = None): # deprecated
    """
    Extracts metadata from a video filename.

    Args:
        filepath (Path): The path to the video file.
        time_format (str, optional): The format of the date in the filename. Defaults to None.

    Returns:
        dict: A dictionary containing the extracted metadata.

    Example return value:
        {
            'examination_date': '2021-03-03',
            'patient_last_name': 'Doe',
            'patient_first_name': 'John',
            'patient_dob': '1990-01-01'
        }
    """
    # get filename without suffix
    filename = filepath.stem    

    if not time_format:
        time_format = "%d.%m.%Y"

    _info = [_.strip() for _ in filename.split("_")]

    if len(_info) == 4:
        examination_date, last_name, first_name, birthdate = _info
    
    else:
        examination_date, last_name, first_name, birthdate = None, None, None, None

    metadata = {}
    metadata['examination_date'] = examination_date
    metadata['patient_last_name'] = last_name
    metadata['patient_first_name'] = first_name
    metadata['patient_dob'] = birthdate

    try:
        metadata['examination_date'] = datetime.datetime.strptime(examination_date, time_format).date()
        metadata['examination_date'] = metadata['examination_date'].strftime("%Y-%m-%d")
    except:
        metadata['examination_date'] = None

    try:
        metadata['patient_dob'] = datetime.datetime.strptime(birthdate, time_format).date()
        metadata['patient_dob'] = metadata['patient_dob'].strftime("%Y-%m-%d")
    except:
        metadata['patient_dob'] = None

    return metadata

def _get_video_metadata(file_path): # Deprecated
    """
    Returns the framerate, dimensions, and duration of a video file.

    Parameters:
    file_path (str): The path to the video file.

    Returns:
    tuple: A tuple containing the framerate (float), dimensions (tuple of ints), and duration (float) of the video.
    """
    # Open the video file
    video = cv2.VideoCapture(file_path)

    # Get the video framerate
    fps = video.get(cv2.CAP_PROP_FPS)

    # Get the video dimensions
    width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dimensions = (width, height)

    # Get the video duration
    frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps

    # Release the video file
    video.release()

    # Return the metadata
    return fps, dimensions, duration
