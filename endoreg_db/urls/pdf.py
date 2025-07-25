from django.urls import path

from endoreg_db.views import (
    PDFStreamView,
    SensitiveMetaDetailView,
    PDFFileForMetaView,
    UpdateSensitiveMetaView,
    RawPdfAnonyTextView,
    UpdateAnonymizedTextView,
    SensitiveMetaVerificationView,
    SensitiveMetaListView,
)

url_patterns = [
    #----------------------------------START : SENSITIVE META AND RAWPDFOFILE PDF PATIENT DETAILS-----------------------------
        
    # Sensitive Meta Detail API (moved before generic pdf/sensitivemeta/ to avoid conflicts)
    # GET /api/pdf/sensitivemeta/<int:sensitive_meta_id>/
    # PATCH /api/pdf/sensitivemeta/<int:sensitive_meta_id>/
    # Used by anonymization store to fetch and update sensitive meta details
    path(
        'pdfstream/<int:pdf_id>/', 
        PDFStreamView.as_view(), name='pdf_stream'
    ),
    path(
        'pdf/sensitivemeta/<int:sensitive_meta_id>/', 
        SensitiveMetaDetailView.as_view(), 
        name='sensitive_meta_detail'
    ),
    # Alternative endpoint for query parameter access (backward compatibility)
    # GET /api/pdf/sensitivemeta/?id=<sensitive_meta_id>
    path(
        'pdf/sensitivemeta/', 
        SensitiveMetaDetailView.as_view(), 
        name='sensitive_meta_query'
    ),
    #The first request (without id) fetches the first available PDF metadata.
    #The "Next" button (with id) fetches the next available PDF.
    #If an id is provided, the API returns the actual PDF file instead of JSON.
    # RENAMED to avoid conflict with new SensitiveMetaDetailView
    path(
        "pdf/meta/", 
        PDFFileForMetaView.as_view(), 
        name="pdf_meta"
    ),  
    # This API endpoint allows updating specific patient details (SensitiveMeta)
    # linked to a PDF record. It enables modifying the patient's first name,
    # last name, date of birth, and examination date.

    # The frontend should send a JSON request body like this:
    # {
    #     "sensitive_meta_id": 2,          # The ID of the SensitiveMeta entry (REQUIRED)
    #     "patient_first_name": "John",    # New first name (OPTIONAL, if provided, cannot be empty)
    #     "patient_last_name": "Doe",      # New last name (OPTIONAL, if provided, cannot be empty)
    #     "patient_dob": "1985-06-15",     # New Date of Birth (OPTIONAL, format YYYY-MM-DD)
    #     "examination_date": "2024-03-20" # New Examination Date (OPTIONAL, format YYYY-MM-DD)
    # }

    # - The frontend sends a PATCH request to this endpoint with the updated patient data.
    # - The backend processes the request and updates only the fields that are provided.
    # - If validation passes, the corresponding SensitiveMeta entry is updated in the database.
    # - If errors occur (e.g., invalid ID, empty fields, incorrect date format), 
    #   the API returns structured error messages.
    path(
        "pdf/update_sensitivemeta/", 
        UpdateSensitiveMetaView.as_view(), 
        name="update_pdf_meta"
    ),
    #  API Endpoint for Fetching PDF Data (Including Anonymized Text)
    # - This endpoint is used when the page loads.
    # - Fetches the first available PDF (if no `last_id` is provided).
    # - If `last_id` is given, fetches the next available PDF.
    # - The frontend calls this endpoint on **page load** and when clicking the **next button**.
    # Example frontend usage:
    #     const url = lastId ? `http://localhost:8000/pdf/anony_text/?last_id=${lastId}` 
    #                        : "http://localhost:8000/pdf/anony_text/";
    path(
        "pdf/anony_text/", 
        RawPdfAnonyTextView.as_view(), 
        name="pdf_anony_text"
    ),  
    #  API Endpoint for Updating the `anonymized_text` Field in `RawPdfFile`
    # - This endpoint is called when the user edits the anonymized text and clicks **Save**.
    # - Updates only the `anonymized_text` field for the specified PDF `id`.
    # - The frontend sends a **PATCH request** to this endpoint with the updated text.
    # Example frontend usage:
    #     axios.patch("http://localhost:8000/pdf/update_anony_text/", { id: 1, anonymized_text: "Updated text" });
    path(
        "pdf/update_anony_text/", 
        UpdateAnonymizedTextView.as_view(), 
        name="update_pdf_anony_text"
    ),
    # ---------------------------------------------------------------------------------------
    # SENSITIVE META API ENDPOINTS & FILE SELECTION
    #
    # New API endpoints for sensitive meta data management and file selection
    # These endpoints support both PDF and video anonymization workflows
    # ---------------------------------------------------------------------------------------

    # Available Files List API
    # GET /api/available-files/?type=pdf|video|all&limit=50&offset=0
    # Lists available PDF and video files for anonymization selection


    # Sensitive Meta Verification API
    # POST /api/pdf/sensitivemeta/verify/
    # For updating verification flags specifically
    path(
        'pdf/sensitivemeta/verify/', 
        SensitiveMetaVerificationView.as_view(), 
        name='sensitive_meta_verify'),

    # Sensitive Meta List API
    # GET /api/pdf/sensitivemeta/list/
    # For listing sensitive meta entries with filtering
    path(
        'pdf/sensitivemeta/list/', 
        SensitiveMetaListView.as_view(), 
        name='sensitive_meta_list'),
]
