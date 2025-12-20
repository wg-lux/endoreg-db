import hashlib
import os
from pathlib import Path


def get_content_hash_filename(file: Path) -> tuple[str, str]:
    """
    Returns a new filename with a uuid - This is the content hash -
    it is used to identify a raw video before processing when no other
    reliable info exists.
    It gets stored in processing_history model.
    """
    # Get the file extension
    file_extension = file.suffix
    # Generate a new file name
    uuid = sha256_file(file)
    new_file_name = f"{uuid}{file_extension}"
    return new_file_name, uuid


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Compute a SHA-256 hash of the file contents in a streaming manner.

    Args:
        path: Path to the file on disk.
        chunk_size: Size of the chunks to read (default: 1MB).

    Returns:
        Hexadecimal SHA-256 digest (64 characters).
    """
    h = hashlib.sha256()
    path_obj = Path(path)

    with path_obj.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def copy_with_progress(src: str, dst: str, buffer_size=1024 * 1024):
    """
    Make a copy of a file with progress bar.

    Args:
        src (str): Source file path.
        dst (str): Destination file path.
        buffer_size (int): Buffer size for copying.
    """
    # Ensure the destination directory exists
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    total_size = os.path.getsize(src)
    copied_size = 0

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
            copied_size += len(buf)
            progress = copied_size / total_size * 100
            print(f"\rProgress: {progress:.2f}%", end="")

    # Print newline once copying is finished so the next log starts on a new line
    print()
