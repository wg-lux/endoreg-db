
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from endoreg_db.models import RawPdfFile
    from django.utils import timezone

class ReportStatusMixin:
    """
    Mixin class for report serializers to provide status, updated_at, and file_type fields.
    """

    def get_status(self, obj:"RawPdfFile") -> Literal['approved'] | Literal['pending']:
        """Ermittelt den Status basierend auf Verarbeitungsstatus"""
        if obj.state_report_processed:
            return 'approved'
        if obj.state_report_processing_required:
            return 'pending'
        return 'pending'

    def get_updated_at(self, obj:"RawPdfFile") -> "timezone.datetime":
        """Simuliert updated_at basierend auf created_at"""
        return obj.created_at if obj.created_at else timezone.now()

    def get_file_type(self, obj:"RawPdfFile"    ) -> "str":
        """Ermittelt den Dateityp basierend auf der Dateiendung"""
        if obj.file:
            return Path(obj.file.name).suffix.lower().lstrip('.')
        return 'unknown'
