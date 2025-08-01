from django.urls import path
from endoreg_db.views import (
    ReportListView,
    ReportWithSecureUrlView,
    SecureFileUrlView,
    ReportFileMetadataView,
    SecureFileServingView,
    validate_secure_url,
)

url_patterns = [        # ---------------------------------------------------------------------------------------
        # REPORT SERVICE ENDPOINTS
        #
        # Neue API-Endpunkte für den Report-Service mit sicheren URLs
        #
        # Diese Endpunkte ermöglichen es dem Frontend (UniversalReportViewer), 
        # Reports mit zeitlich begrenzten, sicheren URLs zu laden und anzuzeigen.
        #
        # Verwendung im Frontend:
        #   - loadReportWithSecureUrl(reportId) 
        #   - generateSecureUrl(reportId, fileType)
        #   - validateCurrentUrl()
        #
        # ---------------------------------------------------------------------------------------

        # API-Endpunkt für paginierte Report-Listen mit Filterung
        # GET /api/reports/?page=1&page_size=20&status=pending&file_type=pdf&patient_name=John
        # Lädt eine paginierte Liste aller Reports mit optionalen Filtern
        path('reports/', 
            ReportListView.as_view(), 
            name='report_list'
        ),

        # API-Endpunkt für Reports mit automatischer sicherer URL-Generierung
        # GET /api/reports/{report_id}/with-secure-url/
        # Lädt Report-Daten inklusive Metadaten und generiert automatisch eine sichere URL
        path(
            'reports/<int:report_id>/with-secure-url/', 
            ReportWithSecureUrlView.as_view(), 
            name='report_with_secure_url'
        ),

        # API-Endpunkt für manuelle sichere URL-Generierung  
        # POST /api/secure-file-urls/
        # Body: {"report_id": 123, "file_type": "pdf"}
        # Generiert eine neue sichere URL für einen bestehenden Report
        path(
            'secure-file-urls/', 
            SecureFileUrlView.as_view(), 
            name='generate_secure_file_url'
        ),

        # API-Endpunkt für Report-Datei-Metadaten
        # GET /api/reports/{report_id}/file-metadata/
        # Gibt Datei-Metadaten zurück (Größe, Typ, Datum, etc.)
        path(
            'reports/<int:report_id>/file-metadata/', 
            ReportFileMetadataView.as_view(), 
            name='report_file_metadata'
        ),

        # Sichere Datei-Serving-Endpunkt mit Token-Validierung
        # GET /api/reports/{report_id}/secure-file/?token={token}
        # Serviert die tatsächliche Datei über eine sichere, tokenbasierte URL
        path(
            'reports/<int:report_id>/secure-file/', 
            SecureFileServingView.as_view(), 
            name='secure_file_serving'
        ),
        # URL-Validierungs-Endpunkt
        # GET /api/validate-secure-url/?url={url}
        # Validiert, ob eine sichere URL noch gültig ist
        path(
            'validate-secure-url/', 
            validate_secure_url, 
            name='validate_secure_url'
        ),
]