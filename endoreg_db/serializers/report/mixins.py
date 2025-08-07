from pathlib import Path
from typing import TYPE_CHECKING, Literal
from django.utils import timezone

if TYPE_CHECKING:
    from endoreg_db.models import RawPdfFile

class ReportStatusMixin:
    """
    Mixin class for report serializers to provide status, updated_at, and file_type fields.
    """

    def get_status(self, obj:"RawPdfFile") -> Literal['approved'] | Literal['pending']:
        """
        Return the report status as 'approved' if the report has been processed, otherwise 'pending'.
        
        Parameters:
            obj (RawPdfFile): The report file instance to check.
        
        Returns:
            Literal['approved'] | Literal['pending']: The status of the report based on its processing state.
        """
        if obj.state_report_processed:
            return 'approved'
        if obj.state_report_processing_required:
            return 'pending'
        return 'pending'

    def get_updated_at(self, obj:"RawPdfFile") -> "timezone.datetime":
        """
        Return the last update time for the given RawPdfFile object.
        
        If the object's creation date is available, it is returned; otherwise, the current time is used.
        """
        return obj.date_created if obj.date_created else timezone.now()

    def get_file_type(self, obj:"RawPdfFile"    ) -> "str":
        """
        Return the file type of the associated file as a lowercase string without the leading dot.
        
        If the object has no file, returns 'unknown'.
        """
        if obj.file:
            return Path(obj.file.name).suffix.lower().lstrip('.')
        return 'unknown'
