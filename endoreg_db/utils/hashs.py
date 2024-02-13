import hashlib

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

