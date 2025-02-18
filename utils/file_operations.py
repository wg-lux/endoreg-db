import shutil
from pathlib import Path
from endoreg_db.utils.uuid import get_uuid

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
    shutil.move(old_file.resolve().as_posix(), new_file.resolve().as_posix())

    return new_file, uuid
