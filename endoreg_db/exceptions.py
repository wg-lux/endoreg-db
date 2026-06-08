import logging

logger = logging.getLogger(__name__)


class InsufficientStorageError(Exception):
    """Raised when there's not enough disk space for an operation."""

    required_space: int
    available_space: int

    def __init__(
        self,
        message: str,
        required_space: int = 0,
        available_space: int = 0,
    ) -> None:
        super().__init__(message)
        self.required_space = required_space
        self.available_space = available_space


class TranscodingError(Exception):
    """Raised when video transcoding fails."""

    pass


class VideoProcessingError(Exception):
    """Base class for video processing errors."""

    pass
