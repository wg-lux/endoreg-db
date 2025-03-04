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


__all__ = [
    "RawPdfFile",
    "RawVideoFile",
]
