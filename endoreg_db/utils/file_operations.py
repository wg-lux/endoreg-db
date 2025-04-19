import shutil
from pathlib import Path
from endoreg_db.utils.uuid import get_uuid
import os

def get_uuid_filename(file:Path) -> tuple[str, str]:
    """
    Returns a new filename with a uuid
    """
    # Get the file extension
    file_extension = file.suffix
    # Generate a new file name
    uuid = get_uuid()
    new_file_name = f"{uuid}{file_extension}"
    return new_file_name, uuid
    
def rename_file_uuid(old_file:Path):
    """
    Rename a file by assigning a uuid while preserving file extension. Returns new filepath and uuid
    """
    # Get the file extension
    file_extension = old_file.suffix
    # Generate a new file name
    uuid = get_uuid()
    new_file_name = f"{uuid}{file_extension}"

    # Rename the file
    new_file = old_file.with_name(new_file_name)
    shutil.move(old_file.as_posix(), new_file.as_posix())

    return new_file, uuid

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
