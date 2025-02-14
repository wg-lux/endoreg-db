import pytesseract
from PIL import Image, ImageOps, ImageFilter
import os
from collections import Counter
from tempfile import TemporaryDirectory
import re
from datetime import datetime
from typing import Dict, List
import numpy as np
from endoreg_db.utils.cropping import crop_and_insert



N_FRAMES_MEAN_OCR = 2

# Helper function to process date strings
def process_date_text(date_text):
    """
    Processes a string of text that represents a date and returns a datetime.date object.

    Args:
        date_text (str): A string of text that represents a date.

    Returns:
        datetime.date: A datetime.date object representing the parsed date, or None if the text cannot be parsed.
    """
    try:
        # Remove any non-digit characters
        date_text_clean = re.sub(r'\D', '', date_text)
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
def process_name_text(name_text):
    """
    Remove all numbers, punctuation, and whitespace from a string of text and return the result.
    """
    name = re.sub(r'[0-9!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~\s]+', '', name_text).strip()
    # capitalize first letter of each word
    name = ' '.join([word.capitalize() for word in name.split()])
    return name


# Helper function to process endoscope type text
def process_general_text(endoscope_text):
    """
    This function takes in a string of text from an endoscope and returns a cleaned version of the text.
    """
    return ' '.join(endoscope_text.split())

def roi_values_valid(roi):
    """
    Check if all values in an ROI dictionary are valid (>=0).
    """
    return all([value >= 0 for value in roi.values()])

# Function to extract text from ROIs
def extract_text_from_rois(image_path, processor):
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
    image_dimensions = image.size # (width, height)

    ####### Adjust Image #######
    # Convert to grayscale
    gray = image.convert('L')

    # Invert colors for white text on black background
    inverted = ImageOps.invert(gray)

    # Initialize the dictionary to hold the extracted text
    extracted_texts = {}

    # Define your ROIs and their corresponding post-processing functions in tuples
    rois_with_postprocessing = [
        ('examination_date', processor.get_roi_examination_date, process_date_text),
        ("patient_first_name", processor.get_roi_patient_first_name, process_name_text),
        ('patient_last_name', processor.get_roi_patient_last_name, process_name_text),
        ('patient_dob', processor.get_roi_patient_dob, process_date_text),
        ('endoscope_type', processor.get_roi_endoscope_type, process_general_text),
        ('endoscope_sn', processor.get_roi_endoscopy_sn, process_general_text),
    ]

    # Extract and post-process text for each ROI
    for roi_name, roi_function, post_process in rois_with_postprocessing:
        # Get the ROI dictionary
        roi = roi_function()
        
        # Check if the ROI has values
        
        if roi_values_valid(roi):
            x, y, w, h = roi['x'], roi['y'], roi['width'], roi['height']
            
            # Get white image with original shape and just the roi remaining
            roi_image = crop_and_insert(inverted, x,y,h,w)

            # OCR configuration: Recognize white text on black background without corrections
            config = '--psm 10 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-üöäÜÖÄß'

            # Use pytesseract to do OCR on the preprocessed ROI
            text = pytesseract.image_to_string(roi_image, config=config).strip()

            # Post-process extracted text
            processed_text = post_process(text)
            
            extracted_texts[roi_name] = processed_text

        else:
            pass

    return extracted_texts

def get_most_frequent_values(rois_texts: Dict[str, List[str]]) -> Dict[str, str]:
    """
    Given a dictionary of ROIs and their corresponding texts, returns a dictionary of the most frequent text for each ROI.

    Args:
        rois_texts: A dictionary where the keys are the names of the ROIs and the values are lists of texts.

    Returns:
        A dictionary where the keys are the names of the ROIs and the values are the most frequent text for each ROI.
    """
    most_frequent = {}
    for key in rois_texts.keys():
        counter = Counter([text for text in rois_texts[key] if text])
        most_frequent[key], _ = counter.most_common(1)[0] if counter else (None, None)
    return most_frequent

def process_video(video_path, processor):
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
        rois_texts = {roi_name: [] for roi_name in processor.get_rois().keys()}
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
                    rois_texts[key].append(text)
                frames_for_mean_extraction += 1

            frame_number += 1

            if frames_for_mean_extraction >= N_FRAMES_MEAN_OCR: break

        # Release the video capture object
        video.release()

        # Get the most frequent values for each ROI
        return get_most_frequent_values(rois_texts)

