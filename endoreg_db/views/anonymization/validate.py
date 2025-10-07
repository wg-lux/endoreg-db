from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from endoreg_db.models import VideoFile, RawPdfFile
from endoreg_db.serializers.anonymization import SensitiveMetaValidateSerializer


class AnonymizationValidateView(APIView):
    """
    POST /api/anonymization/<int:file_id>/validate/
    
    Validiert und aktualisiert SensitiveMeta-Felder für Videos oder PDFs.
    
    Body (Datumsfelder bevorzugt in deutschem Format DD.MM.YYYY; ISO YYYY-MM-DD ebenfalls akzeptiert):
    {
      "patient_first_name": "Max",
      "patient_last_name":  "Mustermann",
      "patient_dob":        "21.03.1994",      // DD.MM.YYYY bevorzugt
      "examination_date":   "15.02.2024",      // DD.MM.YYYY bevorzugt
      "casenumber":         "12345",
      "anonymized_text":    "...",             // nur für PDFs; Videos ignorieren
      "is_verified":        true               // optional; default true
    }
    
    Rückwärtskompatibilität: ISO-Format (YYYY-MM-DD) wird ebenfalls akzeptiert.
    """

    @transaction.atomic
    def post(self, request, file_id: int):
        # Serializer-Validierung mit deutscher Datums-Priorität
        serializer = SensitiveMetaValidateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        payload.setdefault("is_verified", True)

        # Try Video first
        video = VideoFile.objects.filter(pk=file_id).first()
        if video:
            # Ensure center_name is in payload for hash calculation
            if video.center and not payload.get("center_name"):
                payload["center_name"] = video.center.name
                
            ok = video.validate_metadata_annotation(payload)
            if not ok:
                return Response({"error": "Video validation failed."}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"message": "Video validated."}, status=status.HTTP_200_OK)

        # Then PDF
        pdf = RawPdfFile.objects.filter(pk=file_id).first()
        if pdf:
            # Ensure center_name is in payload for hash calculation
            if pdf.center and not payload.get("center_name"):
                payload["center_name"] = pdf.center.name
                
            ok = pdf.validate_metadata_annotation(payload)
            if not ok:
                return Response({"error": "PDF validation failed."}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"message": "PDF validated."}, status=status.HTTP_200_OK)

        return Response({"error": f"Item {file_id} not found as video or pdf."}, status=status.HTTP_404_NOT_FOUND)