from rest_framework import serializers
from django.utils import timezone
from datetime import timedelta
import uuid
from pathlib import Path
from ..models import RawPdfFile, SensitiveMeta

class SecureFileUrlSerializer(serializers.Serializer):
    """
    Serializer für sichere File-URLs mit Ablaufzeit
    """
    url = serializers.URLField()
    expires_at = serializers.DateTimeField()
    file_type = serializers.CharField(max_length=20)
    original_filename = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField()
    
    def create(self, validated_data):
        # Nicht implementiert, da nur für Ausgabe verwendet
        raise NotImplementedError("SecureFileUrlSerializer ist nur für Ausgabe gedacht")
    
    def update(self, instance, validated_data):
        # Nicht implementiert, da nur für Ausgabe verwendet  
        raise NotImplementedError("SecureFileUrlSerializer ist nur für Ausgabe gedacht")
    
class ReportMetaSerializer(serializers.ModelSerializer):
    """
    Serializer für Report-Metadaten basierend auf SensitiveMeta
    """
    # Füge fehlende Zeitstempel-Felder hinzu
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()
    casenumber = serializers.CharField(source='case_number', allow_blank=True, allow_null=True)
    
    class Meta:
        model = SensitiveMeta
        fields = [
            'id', 'patient_first_name', 'patient_last_name', 
            'patient_gender', 'patient_dob', 'examination_date',
            'casenumber', 'created_at', 'updated_at'
        ]
    
    def get_created_at(self, obj):
        """Holt created_at vom SensitiveMeta Model oder nutzt einen Fallback"""
        if hasattr(obj, 'created_at') and obj.created_at:
            return obj.created_at
        # Fallback wenn SensitiveMeta kein created_at hat
        return timezone.now()
    
    def get_updated_at(self, obj):
        """Holt updated_at vom SensitiveMeta Model oder nutzt einen Fallback"""
        if hasattr(obj, 'updated_at') and obj.updated_at:
            return obj.updated_at
        # Fallback wenn SensitiveMeta kein updated_at hat  
        return self.get_created_at(obj)

class ReportDataSerializer(serializers.ModelSerializer):
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
    
    def get_status(self, obj):
        """Ermittelt den Status basierend auf Verarbeitungsstatus"""
        if hasattr(obj, 'state_report_processed') and obj.state_report_processed:
            return 'approved'
        elif hasattr(obj, 'state_report_processing_required') and obj.state_report_processing_required:
            return 'pending'
        else:
            return 'pending'  # Default status
    
    def get_updated_at(self, obj):
        """Simuliert updated_at basierend auf created_at"""
        return obj.created_at if obj.created_at else timezone.now()
    
    def get_file_type(self, obj):
        """Ermittelt den Dateityp basierend auf der Dateiendung"""
        if obj.file:
            return Path(obj.file.name).suffix.lower().lstrip('.')
        return 'unknown'
    
    def to_representation(self, instance):
        """Fügt sichere URL hinzu wenn angefordert"""
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
        """Generiert eine sichere URL mit Ablaufzeit"""
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

class ReportListSerializer(serializers.ModelSerializer):
    """
    Vereinfachter Serializer für Report-Listen
    """
    report_meta = ReportMetaSerializer(source='sensitive_meta', read_only=True)
    file_type = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()
    
    class Meta:
        model = RawPdfFile
        fields = ['id', 'status', 'report_meta', 'file_type', 'created_at', 'updated_at']
    
    def get_status(self, obj):
        """Ermittelt den Status basierend auf Verarbeitungsstatus"""
        if hasattr(obj, 'state_report_processed') and obj.state_report_processed:
            return 'approved'
        elif hasattr(obj, 'state_report_processing_required') and obj.state_report_processing_required:
            return 'pending'
        else:
            return 'pending'
    
    def get_updated_at(self, obj):
        """Simuliert updated_at basierend auf created_at"""
        return obj.created_at if obj.created_at else timezone.now()
    
    def get_file_type(self, obj):
        if obj.file:
            return Path(obj.file.name).suffix.lower().lstrip('.')
        return 'unknown'