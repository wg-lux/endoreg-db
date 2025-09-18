"""
Simple, explicit permission vocabulary using Keycloak realm roles.

- You define roles in Keycloak (e.g., "data:read", "data:write").
- You map each DRF route name to the role it requires.
- Convention: "write ⇒ read"
  (If a user has "data:write", they automatically satisfy "data:read" checks for the same resource.)

This file does NOT create roles in Django. It only references the roles that already exist
and arrive in the ID/Access token from Keycloak. Your OIDC backend syncs those into
Django Groups; PolicyPermission reads the group names and enforces these rules.
"""
REQUIRED_ROLES: dict[str, str] = {}
# -----------------------------
# Route → Role mapping
# -----------------------------
# DRF DefaultRouter route names are usually:
#   "<basename>-list"   for collection endpoints (GET /api/<prefix>/)
#   "<basename>-detail" for item endpoints       (GET /api/<prefix>/{pk}/)
#
# For @action routes on a ViewSet:
#   detail=False → "<basename>-<action_name>"
#   detail=True  → "<basename>-<action_name>"
#
# For function/class-based views with path(..., name="..."):
#   the route name is exactly that "name".
#
# IMPORTANT: The *basename* is either what you passed as basename=... when registering
# the ViewSet, or (if omitted) DRF infers it from the queryset’s model_name singular.
# e.g., router.register("patients", PatientViewSet) → basename inferred as "patient"
#       route names will be "patient-list", "patient-detail".
REQUIRED_ROLES.update({
    # Patients
    "patient-list":   "data:write",  # GET /api/patients/   (list)   → require editors
    "patient-detail": "data:read",   # GET /api/patients/1/ (detail) → readers OK
    # Custom function route you defined:
    "check_pe_exist": "data:read",   # GET /api/check_pe_exist/<id>/

    # (examples for other existing ViewSets)
    # "center-list":   "data:read",
    # "center-detail": "data:read",
    # "gender-list":   "data:read",
    # "gender-detail": "data:read",
    # "patient-findings-list":   "data:read",
    # "patient-findings-detail": "data:read",

    # (optional) DRF API root (GET /api/) route name is "api-root"
    # "api-root": "data:read",
})

# --- Anonymization & Media Management ----------------------------------------
# These names come from endoreg_db/urls/anonymization.py (path(..., name='...')).
# We assign read/write based on whether the endpoint only reads state (read)
# or changes/starts/stops something (write).

REQUIRED_ROLES.update({
    # Overview & status (safe reads)
    "anonymization_items_overview": "data:read",  # GET /api/anonymization/items/overview/  → list overview; no mutation
    "get_anonymization_status":     "data:read",  # GET /api/anonymization/<file_id>/status/ → poll status only
    "polling_coordinator_info":     "data:read",  # GET /api/anonymization/polling-info/     → diagnostics/info; read only
    "media_management_status":      "data:read",  # GET /api/media-management/status/        → status page; read only

    # Actions that mutate state (need write)
    "set_current_for_validation":   "data:write", # POST/GET sets “current” file for validation; server state changes
    "start_anonymization":          "data:write", # triggers processing pipeline for a file
    "validate_anonymization":       "data:write", # submits validation decision / marks as validated
    "clear_processing_locks":       "data:write", # clears any processing locks; operational side-effect
    "media_management_cleanup":     "data:write", # cleanup operation; modifies files/state
    "force_remove_media":           "data:write", # destructive delete of a media file
    "reset_processing_status":      "data:write", # resets state of a media item
})

# --- Examination & PatientExamination ----------------------------------------
# Routes from endoreg_db/urls/examination.py
# Rule of thumb:
#   - Pure lookups (reads) → data:read
#   - Creates / mutations → data:write
#   - For views that support both GET and PATCH, we DO NOT pin a single role here,
#     so the DEFAULT_ROLE_BY_METHOD fallback applies:
#         GET   → data:read
#         PATCH → data:write

REQUIRED_ROLES.update({
    # Read-only helpers (classification / findings lookups)
    "get_findings_for_examination":       "data:read",  # GET /api/examinations/<examination_id>/findings/
    "get_classifications_for_finding":    "data:read",  # GET /api/findings/<finding_id>/classifications/
    "get_choices_for_classification":     "data:read",  # GET /api/classifications/<classification_id>/choices/
    "get_classifications_for_examination":"data:read",  # GET /api/patient-examinations/<exam_id>/classifications/
    "get_patient_examination_findings":   "data:read",  # GET /api/patient-examinations/<examination_id>/findings/

    # Create/list PatientExamination (explicit creates need write; list is read)
    "patient_examination_create":         "data:write", # POST /api/patient-examinations/create/
    "patient_examination_list":           "data:read",  # GET  /api/patient-examinations/list/
    # IMPORTANT: do NOT add "patient_examination_detail" here so method fallback applies:
    #   GET    /api/patient-examinations/<pk>/  → data:read
    #   PATCH  /api/patient-examinations/<pk>/  → data:write
})


# -----------------------------
# Sensible fallback by HTTP method
# -----------------------------
# If a route name is NOT listed in REQUIRED_ROLES,
# the permission falls back to this per-method mapping.
DEFAULT_ROLE_BY_METHOD = {
    "GET":     "data:read",
    "HEAD":    "data:read",
    "OPTIONS": "data:read",
    "POST":    "data:write",
    "PUT":     "data:write",
    "PATCH":   "data:write",
    "DELETE":  "data:write",
}

# -----------------------------
# Role satisfaction rule
# -----------------------------
# Implements the "write ⇒ read" convention:
#   - exact match satisfies (needed in user_roles)
#   - if needed ends with ":read", having the sibling ":write" grants it
def satisfies(user_roles: set[str], needed: str) -> bool:
    """Return True if user_roles satisfy the needed role with the write⇒read rule."""
    if needed in user_roles:
        return True
    if needed.endswith(":read"):
        base = needed.rsplit(":", 1)[0]
        return f"{base}:write" in user_roles
    return False
