import cv2


def _calc_duration(self, obj):
    """
    Calculate duration using OpenCV if not already set.
    """
    if not obj.raw_file:
        return None
    cap = cv2.VideoCapture(str(obj.raw_file.path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0 else 0
    cap.release()
    return duration