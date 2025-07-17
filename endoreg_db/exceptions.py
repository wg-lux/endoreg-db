

class InsufficientStorageError(Exception):
    """Raised when there's not enough disk space for an operation."""
    
    def __init__(self, message, required_space=None, available_space=None):
        super().__init__(message)
        self.required_space = required_space
        self.available_space = available_space


class TranscodingError(Exception):
    """Raised when video transcoding fails."""
    pass


class VideoProcessingError(Exception):
    """Base class for video processing errors."""
    pass