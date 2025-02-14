import os
from pathlib import Path

from .raw_video import RawVideoFile
from .raw_pdf import RawPdfFile

# FileImporter class
# This class is used to import data from a file into the database.
# Expects a directory containing files to import.
# creates correct import file object depending on file type

# FileImporter class
# This class is used to import data from a file into the database by creating objects for the files.
# main method is import_files which expects a path to a directory containing files to import.
# creates correct import file object depending on file type by checking the file extension

class FileImporter:
    def __init__(self, directory):
        self.directory = directory

    def import_files(self):
        directory_path = Path(self.directory)
        for file in directory_path.iterdir():
            if file.is_file():
                if file.suffix.lower() in ['.mov', '.mp4']:
                    RawVideoFile.create_from_file(file)
                else:
                    raise ValueError(f"File type {file.suffix} not supported")
            else:
                raise ValueError(f"{file} is not a file")
            
        