import os
import re
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, cast

import cv2
import pytesseract
from PIL import Image, ImageOps
from endoreg_db.utils.cropping import crop_and_insert
from lx_dtypes.models.contracts.endoscopy_processor import RoiBoxCore


N_FRAMES_MEAN_OCR = 2


class _OcrProcessorLike(Protocol):
    def get_roi_examination_date(self) -> RoiBoxCore: ...

    def get_roi_patient_first_name(self) -> RoiBoxCore: ...

    def get_roi_patient_last_name(self) -> RoiBoxCore: ...

    def get_roi_patient_dob(self) -> RoiBoxCore: ...

    def get_roi_endoscope_type(self) -> RoiBoxCore | None: ...

    def get_roi_endoscopy_sn(self) -> RoiBoxCore | None: ...

    def get_rois(self) -> dict[str, RoiBoxCore | None]: ...


# Helper function to process date strings
def process_date_text(date_text: str) -> date | None:
    """
    Processes a string of text that represents a date and returns a datetime.date object.

    Args:
        date_text (str): A string of text that represents a date.

    Returns:
        datetime.date: A datetime.date object representing the parsed date, or None if the text cannot be parsed.
    """
    try:
        # Remove any non-digit characters
        date_text_clean = re.sub(r"\D", "", date_text)
        # Reformat to 'ddmmyyyy' if necessary
        if len(date_text_clean) == 8:
            return datetime.strptime(date_text_clean, "%d%m%Y").date()
        elif len(date_text_clean) == 14:
            return datetime.strptime(date_text_clean, "%d%m%Y%H%M%S").date()
    except ValueError:
        # Return None if the text cannot be parsed into a date
        # set date to 1/1/1900
        return datetime.strptime("01011900", "%d%m%Y").date()


# Helper function to process patient names
def process_name_text(name_text: str) -> str:
    """
    Remove all numbers, punctuation, and whitespace from a string of text and return the result.
    """
    name = re.sub(r'[0-9!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~\s]+', "", name_text).strip()
    # capitalize first letter of each word
    name = " ".join([word.capitalize() for word in name.split()])
    return name


# Helper function to process endoscope type text
def process_general_text(endoscope_text: str) -> str:
    """
    This function takes in a string of text from an endoscope and returns a cleaned version of the text.
    """
    return " ".join(endoscope_text.split())


def roi_values_valid(roi: RoiBoxCore) -> bool:
    """
    Check if all values in an ROI dictionary are valid (>=0).
    """
    return roi.x >= 0 and roi.y >= 0 and roi.width >= 0 and roi.height >= 0


# Function to extract text from ROIs
def extract_text_from_rois(
    image_path: str | Path, processor: _OcrProcessorLike
) -> dict[str, str | None]:
    """
    Extracts text from regions of interest (ROIs) in an image using OCR.

    Args:
        image_path (str): The path to the image file.
        processor (EndoscopyProcessor): An instance of the EndoscopyProcessor class.

    Returns:
        dict: A dictionary containing the extracted text for each ROI.
    """
    # Read the image using Pillow
    image = Image.open(image_path)
    ####### Adjust Image #######
    # Convert to grayscale
    gray = image.convert("L")

    # Invert colors for white text on black background
    inverted = ImageOps.invert(gray)

    # Initialize the dictionary to hold the extracted text
    extracted_texts: dict[str, str | None] = {}

    # Define your ROIs and their corresponding post-processing functions in tuples
    rois_with_postprocessing: list[
        tuple[str, Callable[[], RoiBoxCore | None], Callable[[str], str | date | None]]
    ] = [
        ("examination_date", processor.get_roi_examination_date, process_date_text),
        ("patient_first_name", processor.get_roi_patient_first_name, process_name_text),
        ("patient_last_name", processor.get_roi_patient_last_name, process_name_text),
        ("patient_dob", processor.get_roi_patient_dob, process_date_text),
        ("endoscope_type", processor.get_roi_endoscope_type, process_general_text),
        ("endoscope_sn", processor.get_roi_endoscopy_sn, process_general_text),
    ]

    # Extract and post-process text for each ROI
    for roi_name, roi_function, post_process in rois_with_postprocessing:
        # Get the ROI dictionary
        roi = roi_function()

        # Check if the ROI has values

        if roi is not None and roi_values_valid(roi):
            x, y, w, h = roi.x, roi.y, roi.width, roi.height

            # Get white image with original shape and just the roi remaining
            roi_image = crop_and_insert(inverted, x, y, h, w)

            # OCR configuration: Recognize white text on black background without corrections
            # Use pytesseract to do OCR on the preprocessed ROI
            image_to_string = cast(
                "Callable[[object], str]", getattr(pytesseract, "image_to_string")
            )
            ocr_result = image_to_string(roi_image)
            text = str(ocr_result).strip()

            # Post-process extracted text
            processed_raw = post_process(text)
            processed_text = (
                processed_raw.isoformat()
                if isinstance(processed_raw, date)
                else processed_raw
            )

            extracted_texts[roi_name] = processed_text

    return extracted_texts


def get_most_frequent_values(rois_texts: dict[str, list[str]]) -> dict[str, str]:
    """
    Given a dictionary of ROIs and their corresponding texts, returns a dictionary of the most frequent text for each ROI.

    Args:
        rois_texts: A dictionary where the keys are the names of the ROIs and the values are lists of texts.

    Returns:
        A dictionary where the keys are the names of the ROIs and the values are the most frequent text for each ROI.
    """
    most_frequent: dict[str, str] = {}
    for key in rois_texts.keys():
        counter = Counter([text for text in rois_texts[key] if text])
        if counter:
            most_frequent[key] = counter.most_common(1)[0][0]
        else:
            most_frequent[key] = ""
    return most_frequent


def process_video(
    video_path: str | Path, processor: _OcrProcessorLike
) -> dict[str, str]:
    """
    Processes a video file by extracting text from regions of interest (ROIs) in each frame.

    Args:
        video_path (str): The path to the video file to process.
        processor (OCRProcessor): An instance of the OCRProcessor class that defines the ROIs to extract text from.

    Returns:
        dict: A dictionary containing the most frequent text values extracted from each ROI.
    """
    # Create a temporary directory to store frames
    with TemporaryDirectory() as temp_dir:
        # Capture the video
        video = cv2.VideoCapture(video_path)
        success, frame_number = True, 0
        rois_texts: dict[str, list[str]] = {
            "examination_date": [],
            "patient_first_name": [],
            "patient_last_name": [],
            "patient_dob": [],
            "endoscope_type": [],
            "endoscope_sn": [],
        }
        frames_for_mean_extraction = 0

        while success:
            success, frame = video.read()

            # Check if this is the 200th frame
            if frame_number % 1000 == 0 and success:
                frame_path = os.path.join(temp_dir, f"frame_{frame_number}.jpg")
                cv2.imwrite(frame_path, frame)  # Save the frame as a JPEG file
                # cv2.imwrite(f"_tmp/frame_{frame_number}.jpg", frame)

                # Extract text from ROIs
                extracted_texts = extract_text_from_rois(frame_path, processor)

                # Store the extracted text from each ROI
                for key, text in extracted_texts.items():
                    rois_texts[key].append("" if text is None else text)
                frames_for_mean_extraction += 1

            frame_number += 1

            if frames_for_mean_extraction >= N_FRAMES_MEAN_OCR:
                break

        # Release the video capture object
        video.release()

        # Get the most frequent values for each ROI
        return get_most_frequent_values(rois_texts)
