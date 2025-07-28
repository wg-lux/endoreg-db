from endoreg_db.models import RawPdfFile
from endoreg_db.serializers.meta.report_meta import ReportMetaSerializer
from endoreg_db.serializers.report.secure_file_url import SecureFileUrlSerializer
from endoreg_db.serializers.report.mixins import ReportStatusMixin

from django.utils import timezone
from rest_framework import serializers


import uuid
from datetime import timedelta
from pathlib import Path


class ReportDataSerializer(ReportStatusMixin, serializers.ModelSerializer):
    """
    Hauptserializer für Report-Daten mit sicherer URL
    """
    report_meta = ReportMetaSerializer(source='sensitive_meta', read_only=True)
    secure_file_url = SecureFileUrlSerializer(read_only=True)
    file_type = serializers.SerializerMethodField()

    # Status und updated_at hinzufügen (da sie im RawPdfFile Model fehlen)
    status = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = RawPdfFile
        fields = [
            'id', 'anonymized_text', 'status', 'report_meta',
            'secure_file_url', 'file_type', 'created_at', 'updated_at'
        ]

    def get_updated_at(self, obj):
        """
        Return the updated timestamp for the object, using `created_at` if available or the current time otherwise.
        """
        return obj.created_at if obj.created_at else timezone.now()

    def get_file_type(self, obj):
        """
        Return the file extension of the associated file in lowercase, or 'unknown' if no file is present.
        
        Parameters:
            obj: The object instance containing the file.
        
        Returns:
            str: The file extension without the leading dot, or 'unknown' if the file is missing.
        """
        if obj.file:
            return Path(obj.file.name).suffix.lower().lstrip('.')
        return 'unknown'

    def to_representation(self, instance):
        """
        Returns the serialized representation of the instance, including a secure file URL if a file exists and a request context is available.
        """
        data = super().to_representation(instance)

        # Sichere URL generieren wenn file vorhanden
        if instance.file and hasattr(instance.file, 'url'):
            request = self.context.get('request')
            if request:
                # Sichere URL mit Token generieren
                secure_url_data = self._generate_secure_url(instance, request)
                data['secure_file_url'] = secure_url_data

        return data

    def _generate_secure_url(self, instance, request):
        """
        Generate a secure URL for downloading the file associated with the given instance, including an expiration time and file metadata.
        
        Parameters:
            instance: The model instance containing the file.
            request: The HTTP request object used to build the absolute URL.
        
        Returns:
            dict: A dictionary containing the secure URL, expiration timestamp, file type, original filename, and file size in bytes.
        """
        # Token für sichere URL generieren
        token = str(uuid.uuid4())
        expires_at = timezone.now() + timedelta(hours=2)  # 2 Stunden gültig

        # URL zusammenbauen
        secure_url = request.build_absolute_uri(
            f"/api/reports/{instance.id}/secure-file/?token={token}"
        )

        # Dateigröße ermitteln
        file_size = 0
        try:
            if instance.file:
                file_size = instance.file.size
        except OSError:
            # Datei nicht verfügbar oder Berechtigung fehlt
            file_size = 0

        return {
            'url': secure_url,
            'expires_at': expires_at,
            'file_type': self.get_file_type(instance),
            'original_filename': Path(instance.file.name).name if instance.file else 'unknown',
            'file_size': file_size
        }
