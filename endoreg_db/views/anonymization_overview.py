# endoreg_db/api/views/anonymization.py

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny    # or DEBUG_PERMISSIONS
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
from endoreg_db.services.anonymization import AnonymizationService

PERMS = DEBUG_PERMISSIONS   # shorten

# ---------- status ------------------------------------------------------
@api_view(["GET"])
@permission_classes(PERMS)
def anonymization_status(request, file_id: int):
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "file_id": file_id,
        "file_type": info["type"],
        "anonymizationStatus": info["status"],
    })

# ---------- start -------------------------------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def start_anonymization(request, file_id: int):
    kind = AnonymizationService.start(file_id)
    if not kind:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"detail": f"Anonymization started for {kind} file"})

# ---------- validate ----------------------------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def validate_anonymization(request, file_id: int):
    kind = AnonymizationService.validate(file_id)
    if not kind:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"detail": f"Anonymization validated for {kind} file"})
