import hashlib
from pathlib import Path

def get_video_hash(video_path):
    """
    Get the hash of a video file.
    """
    # Open the video file in read-binary mode:
    with open(video_path, 'rb') as f:
        # Create the hash object, passing in the video contents for hashing:
        hash_object = hashlib.sha256(f.read())
        # Get the hexadecimal representation of the hash
        video_hash = hash_object.hexdigest()
        assert len(video_hash) <= 255, "Hash length exceeds 255 characters"

    return video_hash

def get_pdf_hash(pdf_path:Path):
    """
    Get the hash of a pdf file.
    """
    pdf_hash = None

    # Open the file in binary mode and read its contents
    with open(pdf_path, 'rb') as f:
        pdf_contents = f.read()
        # Create a hash object using SHA-256 algorithm

    hash_object = hashlib.sha256(pdf_contents, usedforsecurity=False)
    # Get the hexadecimal representation of the hash
    pdf_hash = hash_object.hexdigest()
    assert len(pdf_hash) <= 255, "Hash length exceeds 255 characters"

    return pdf_hash